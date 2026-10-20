"""Checksum, decompression, and package-loader tests without network access."""

import copy
import gzip
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from biometaharmonizer import country_reference as loader


def reference():
    data = {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
            "features": [{"type": "Feature", "properties": {"BMH_ISO_A2": "US"},
                          "geometry": {"type": "Polygon", "coordinates": [[[0,0],[1,0],[1,1],[0,0]]]}}]}
    raw = json.dumps(data).encode()
    payload = gzip.compress(raw, mtime=0)
    manifest = {"schema_version": 2, "crs_verified": True, "crosswalk_audited": True,
                "compressed_bytes": len(payload), "uncompressed_bytes": len(raw),
                "compressed_sha256": hashlib.sha256(payload).hexdigest(),
                "prepared_sha256": hashlib.sha256(raw).hexdigest(),
                "prepared_git_blob_sha1": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
                "feature_count": 1, "crs": data["crs"]["properties"]["name"],
                "country_code_field": "BMH_ISO_A2"}
    return data, payload, manifest


def test_decode_valid_and_preserve_manifest():
    data, payload, manifest = reference()
    before = copy.deepcopy(manifest)
    assert loader._decode_reference(payload, manifest) == data
    assert manifest == before


@pytest.mark.parametrize("field,value", [
    ("schema_version", 1), ("crs_verified", False), ("crosswalk_audited", False),
    ("compressed_sha256", "bad"), ("prepared_sha256", "bad"),
    ("compressed_bytes", 0), ("uncompressed_bytes", -1), ("feature_count", True),
    ("uncompressed_bytes", 100000000), ("country_code_field", ""),
    ("crs", "EPSG:3857"),
])
def test_manifest_rejections(field, value):
    _, payload, manifest = reference()
    manifest[field] = value
    with pytest.raises(ValueError):
        loader._decode_reference(payload, manifest)


def test_compressed_checksum_and_size():
    _, payload, manifest = reference()
    with pytest.raises(ValueError, match="size"):
        loader._decode_reference(payload + b"x", manifest)
    tampered = payload[:-1] + bytes([payload[-1] ^ 1])
    with pytest.raises(ValueError, match="checksum"):
        loader._decode_reference(tampered, manifest)


@pytest.mark.parametrize("field,value,message", [
    ("uncompressed_bytes", 1, "size"),
    ("prepared_sha256", "0" * 64, "checksum"),
    ("feature_count", 2, "count"),
])
def test_uncompressed_checks(field, value, message):
    _, payload, manifest = reference()
    manifest[field] = value
    with pytest.raises(ValueError, match=message):
        loader._decode_reference(payload, manifest)


def test_invalid_gzip():
    _, _, manifest = reference()
    payload = b"not gzip"
    manifest["compressed_bytes"] = len(payload)
    manifest["compressed_sha256"] = hashlib.sha256(payload).hexdigest()
    with pytest.raises(ValueError, match="Invalid compressed"):
        loader._decode_reference(payload, manifest)


def test_missing_resource(tmp_path, monkeypatch):
    monkeypatch.setattr(loader.resources, "files", lambda name: tmp_path)
    loader.load_bundled_country_lookup.cache_clear()
    with pytest.raises(FileNotFoundError, match="missing"):
        loader.load_bundled_country_lookup()
    loader.load_bundled_country_lookup.cache_clear()


def test_bundled_loader_wiring_and_cache(tmp_path, monkeypatch):
    data, payload, manifest = reference()
    (tmp_path / "data").mkdir()
    (tmp_path / "schemas").mkdir()
    (tmp_path / "data" / "country_polygons.geojson.gz").write_bytes(payload)
    (tmp_path / "schemas" / "country_reference_source.json").write_text(json.dumps(manifest))
    calls = []

    class FakeLookup:
        def __init__(self, actual, **kwargs):
            calls.append((actual, kwargs))

    monkeypatch.setattr(loader.resources, "files", lambda name: tmp_path)
    monkeypatch.setitem(sys.modules, "pycountry", SimpleNamespace(countries=[SimpleNamespace(alpha_2="US", name="United States")]))
    monkeypatch.setitem(sys.modules, "biometaharmonizer.coordinate_geo", SimpleNamespace(CoordinateCountryLookup=FakeLookup))
    loader.load_bundled_country_lookup.cache_clear()
    try:
        first = loader.load_bundled_country_lookup()
        assert loader.load_bundled_country_lookup() is first
        assert len(calls) == 1
        assert calls[0][0] == data
        assert calls[0][1] == {"valid_countries": {"US": "United States"}, "country_code_field": "BMH_ISO_A2"}
        assert first.source_sha256 == manifest["prepared_sha256"]
    finally:
        loader.load_bundled_country_lookup.cache_clear()
