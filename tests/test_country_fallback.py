"""Country fallback policy tests with injected lookup results; no network."""

from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from biometaharmonizer.postprocess import postprocess

def frame(**values):
    row = {"biosample_accession": "SAMN001", "lat_lon": "48.8566 N 2.3522 E",
           "geo_country": None, "geo_iso3166": None, "one_health_category": "Human"}
    row.update(values)
    return pd.DataFrame([row])

def lookup(country="France", code="FR", status="matched"):
    return Mock(lookup=Mock(return_value=SimpleNamespace(country=country, country_code=code, status=status)))

def test_fill_and_preserve_native_data():
    original = frame()
    before = original.copy(deep=True)
    result, report = postprocess(original, country_lookup=lookup())
    assert result.iloc[0]["country"] == "France"
    assert result.iloc[0]["countryCode"] == "FR"
    assert report.iloc[0]["country_assignment_source"] == "coordinates"
    assert report.iloc[0]["coordinate_datum_status"] == "assumed_wgs84"
    assert len(result.columns) == len(original.columns) + 10
    assert_frame_equal(result[original.columns], before)
    assert_frame_equal(original, before)

@pytest.mark.parametrize("country,code,status", [
    ("France", "FR", "text_agrees_with_coordinates"),
    ("Germany", "DE", "text_coordinate_disagreement"),
    ("unknown country text", None, "partial_text_assignment_retained"),
    (None, "DE", "text_coordinate_disagreement"),
    (None, "NA", "text_coordinate_disagreement"),
])
def test_keep_text_and_partial_assignments(country, code, status):
    result, report = postprocess(frame(geo_country=country, geo_iso3166=code), country_lookup=lookup())
    assert result.iloc[0]["country"] == country
    assert result.iloc[0]["countryCode"] == code
    assert report.iloc[0]["country_assignment_status"] == status
    assert report.iloc[0]["country_assignment_source"] == "text"

@pytest.mark.parametrize("marker", ["HISTORICAL", "XK"])
def test_special_assignments_not_replaced(marker):
    resolver = lookup()
    _, report = postprocess(frame(geo_country="original", geo_iso3166=marker), country_lookup=resolver)
    resolver.lookup.assert_not_called()
    assert report.iloc[0]["country_assignment_status"] == "historical_or_special_retained"

@pytest.mark.parametrize("status", ["no_polygon_match", "boundary_or_ambiguous", "unmapped_reference_code"])
def test_unresolved_lookup_abstains(status):
    result, report = postprocess(frame(), country_lookup=lookup(None, None, status))
    assert pd.isna(result.iloc[0]["countryCode"])
    assert report.iloc[0]["country_lookup_status"] == status

@pytest.mark.parametrize("datum,expected,called", [
    ("WGS84", "declared_wgs84", True),
    ("EPSG:4326", "declared_wgs84", True),
    ("EPSG:3857", "unsupported_or_conflicting_datum", False),
])
def test_declared_datum(datum, expected, called):
    resolver = lookup()
    _, report = postprocess(frame(_extra_attributes={"geodeticDatum": datum}), country_lookup=resolver)
    assert report.iloc[0]["coordinate_datum_status"] == expected
    assert resolver.lookup.called == called

def test_missing_or_conflicting_coordinates_skip_lookup():
    resolver = lookup()
    for data in [frame(lat_lon=None), frame(_extra_attributes={"latitude": 40, "longitude": 3})]:
        _, report = postprocess(data, country_lookup=resolver)
        assert report.iloc[0]["country_lookup_status"] == "coordinates_unavailable"
    resolver.lookup.assert_not_called()

def test_load_once_and_preserve_duplicate_indices(monkeypatch):
    resolver = lookup()
    loader = Mock(return_value=resolver)
    monkeypatch.setattr("biometaharmonizer.country_reference.load_bundled_country_lookup", loader)
    original = pd.concat([frame(), frame()])
    original.index = [7, 7]
    result, report = postprocess(original)
    assert result.index.tolist() == [7, 7]
    assert report["row_position"].tolist() == [0, 1]
    assert report["country_assignment_status"].tolist() == ["filled_from_coordinates"] * 2
    loader.assert_called_once()

def test_disable_does_not_load_reference(monkeypatch):
    loader = Mock(side_effect=AssertionError("must not load"))
    monkeypatch.setattr("biometaharmonizer.country_reference.load_bundled_country_lookup", loader)
    result, report = postprocess(frame(), infer_country=False)
    assert pd.isna(result.iloc[0]["countryCode"])
    assert "country_lookup_status" not in report.columns
    loader.assert_not_called()

def test_reference_failure_propagates(monkeypatch):
    monkeypatch.setattr("biometaharmonizer.country_reference.load_bundled_country_lookup", Mock(side_effect=FileNotFoundError("missing")))
    with pytest.raises(FileNotFoundError, match="missing"):
        postprocess(frame())
