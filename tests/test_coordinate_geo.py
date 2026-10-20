"""Synthetic geometry tests, not validation of real-world country boundaries."""

import copy
import hashlib
import json

import pytest

pytest.importorskip("shapely", minversion="2.0")
from biometaharmonizer.coordinate_geo import CoordinateCountryLookup

COUNTRIES = {"US": "United States", "CA": "Canada", "FJ": "Fiji"}


def ring(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


def feature(code, coordinates=None):
    return {"type": "Feature", "properties": {"code": code},
            "geometry": {"type": "Polygon", "coordinates": [coordinates or ring(0, 0, 10, 10)]}}


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def lookup(*features):
    return CoordinateCountryLookup(collection(*features), valid_countries=COUNTRIES, country_code_field="code")


def test_interior_coordinate_order_and_canonical_name():
    engine = lookup(feature("US", ring(20, 0, 30, 10)))
    result = engine.lookup(5, 25)
    assert result == ("United States", "US", "matched")
    assert engine.lookup(25, 5).status == "no_polygon_match"


def test_border_and_outside_abstain():
    engine = lookup(feature("US"), feature("CA", ring(10, 0, 20, 10)))
    assert engine.lookup(5, 10).status == "boundary_or_ambiguous"
    assert engine.lookup(30, 30).status == "no_polygon_match"


def test_overlap_with_different_codes_abstains():
    engine = lookup(feature("US"), feature("CA", ring(5, 0, 15, 10)))
    assert engine.lookup(5, 7).status == "boundary_or_ambiguous"


def test_overlap_with_same_code_is_consistent():
    engine = lookup(feature("US"), feature("US", ring(5, 0, 15, 10)))
    assert engine.lookup(5, 7).country_code == "US"


def test_unmapped_geometry_not_silently_dropped():
    engine = lookup(feature("XK"))
    assert engine.unmapped_feature_indices == (0,)
    assert engine.lookup(5, 5).status == "unmapped_reference_code"


def test_holes_and_hole_boundaries():
    item = feature("US")
    item["geometry"]["coordinates"].append(ring(2, 2, 4, 4))
    engine = lookup(item)
    assert engine.lookup(3, 3).status == "no_polygon_match"
    assert engine.lookup(3, 2).status == "boundary_or_ambiguous"


def test_multipolygon_antimeridian_parts():
    item = feature("FJ")
    item["geometry"] = {"type": "MultiPolygon", "coordinates": [[ring(179, -2, 180, 2)], [ring(-180, -2, -179, 2)]]}
    engine = lookup(item)
    assert engine.lookup(0, 179.5).country_code == "FJ"
    assert engine.lookup(0, -179.5).country_code == "FJ"
    assert engine.lookup(0, 0).status == "no_polygon_match"


@pytest.mark.parametrize("latitude,longitude", [
    (None, 0), (float("nan"), 0), (0, float("inf")),
    (91, 0), (0, 181), (True, 0), (0, False), ("bad", 0),
])
def test_invalid_coordinates(latitude, longitude):
    assert lookup(feature("US")).lookup(latitude, longitude).status == "invalid_coordinates"


def test_cache_and_no_input_mutation():
    data = collection(feature("US"))
    before = copy.deepcopy(data)
    engine = CoordinateCountryLookup(data, valid_countries=COUNTRIES, country_code_field="code")
    engine.lookup(5, 5)
    engine.lookup(5, 5)
    assert engine._cached_lookup.cache_info().hits == 1
    assert data == before


@pytest.mark.parametrize("kind", ["empty", "missing_code", "non_polygon", "invalid", "unclosed", "range", "dateline"])
def test_bad_reference_rejected(kind):
    item = feature("US")
    data = collection(item)
    if kind == "empty":
        data["features"] = []
    elif kind == "missing_code":
        item["properties"] = {}
    elif kind == "non_polygon":
        item["geometry"] = {"type": "Point", "coordinates": [0, 0]}
    elif kind == "invalid":
        item["geometry"]["coordinates"] = [[[0, 0], [10, 10], [0, 10], [10, 0], [0, 0]]]
    elif kind == "unclosed":
        item["geometry"]["coordinates"][0].pop()
    elif kind == "range":
        item["geometry"]["coordinates"] = [ring(0, 0, 181, 10)]
    else:
        item["geometry"]["coordinates"] = [ring(-179, 0, 179, 10)]
    with pytest.raises(ValueError):
        CoordinateCountryLookup(data, valid_countries=COUNTRIES, country_code_field="code")


def test_verified_file_and_crs(tmp_path):
    raw = json.dumps(collection(feature("US"))).encode()
    path = tmp_path / "boundaries.geojson"
    path.write_bytes(raw)
    expected = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    kwargs = dict(expected_git_blob_sha1=expected, valid_countries=COUNTRIES, country_code_field="code", crs="EPSG:4326")
    engine = CoordinateCountryLookup.from_geojson(path, **kwargs)
    assert engine.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert engine.lookup(5, 5).country_code == "US"
    with pytest.raises(ValueError, match="checksum"):
        CoordinateCountryLookup.from_geojson(path, **dict(kwargs, expected_git_blob_sha1="0" * 40))
    with pytest.raises(ValueError, match="EPSG:4326"):
        CoordinateCountryLookup.from_geojson(path, **dict(kwargs, crs="EPSG:3857"))


def test_invalid_country_dictionary():
    with pytest.raises(ValueError):
        CoordinateCountryLookup(collection(feature("US")), valid_countries={"USA": "United States"}, country_code_field="code")
