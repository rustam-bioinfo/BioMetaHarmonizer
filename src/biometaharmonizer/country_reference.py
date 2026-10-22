"""Load the audited, gzip-compressed country reference from package data.

No network access or on-the-fly geometry repair occurs here. This loader is not
connected to post-processing yet; the binary resource must first be installed.
"""

import functools
import gzip
import hashlib
import io
import json
import re
from importlib import resources


_MAX_REFERENCE_BYTES = 64 * 1024 * 1024


def _decode_reference(payload, manifest):
    """Verify compressed and uncompressed bytes before reading GeoJSON."""
    if manifest.get("schema_version") != 2:
        raise ValueError("Unsupported country-reference manifest version")
    if manifest.get("crs_verified") is not True or manifest.get("crosswalk_audited") is not True:
        raise ValueError("Country reference has not passed its CRS and code audit")
    for field in ("compressed_sha256", "prepared_sha256"):
        value = manifest.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Missing or invalid reference checksum: " + field)
    for field in ("compressed_bytes", "uncompressed_bytes", "feature_count"):
        value = manifest.get(field)
        if type(value) is not int or value <= 0:
            raise ValueError("Invalid reference size or count: " + field)
    if manifest["uncompressed_bytes"] > _MAX_REFERENCE_BYTES:
        raise ValueError("Country reference exceeds the decompression limit")
    if len(payload) != manifest["compressed_bytes"]:
        raise ValueError("Compressed country-reference size mismatch")
    if hashlib.sha256(payload).hexdigest() != manifest["compressed_sha256"]:
        raise ValueError("Compressed country-reference checksum mismatch")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as handle:
            raw = handle.read(manifest["uncompressed_bytes"] + 1)
    except (OSError, EOFError) as exc:
        raise ValueError("Invalid compressed country reference") from exc
    if len(raw) != manifest["uncompressed_bytes"]:
        raise ValueError("Uncompressed country-reference size mismatch")
    if hashlib.sha256(raw).hexdigest() != manifest["prepared_sha256"]:
        raise ValueError("Uncompressed country-reference checksum mismatch")
    data = json.loads(raw)
    if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise ValueError("Country reference must be a FeatureCollection")
    if len(data["features"]) != manifest["feature_count"]:
        raise ValueError("Country-reference feature count mismatch")
    actual_crs = data.get("crs", {}).get("properties", {}).get("name")
    if manifest.get("crs") != "urn:ogc:def:crs:OGC:1.3:CRS84" or actual_crs != manifest["crs"]:
        raise ValueError("Country-reference CRS metadata mismatch")
    code_field = manifest.get("country_code_field")
    if not isinstance(code_field, str) or not code_field:
        raise ValueError("An audited country-code field is required")
    return data


@functools.lru_cache(maxsize=1)
def load_bundled_country_lookup():
    """Return a cached offline lookup using canonical names from pycountry.

    Shapely is imported only when the lookup object is constructed. Sample datum
    assumptions and fallback policy belong to the future post-processing caller.
    """
    base = resources.files("biometaharmonizer")
    manifest_file = base / "schemas" / "country_reference_source.json"
    reference_file = base / "data" / "country_polygons.geojson.gz"
    if not reference_file.is_file():
        raise FileNotFoundError("Bundled country_polygons.geojson.gz is missing; install the prepared reference resource")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    data = _decode_reference(reference_file.read_bytes(), manifest)
    import pycountry
    from biometaharmonizer.coordinate_geo import CoordinateCountryLookup

    countries = {country.alpha_2: country.name for country in pycountry.countries}
    lookup = CoordinateCountryLookup(
        data, valid_countries=countries,
        country_code_field=manifest["country_code_field"],
    )
    lookup.source_git_blob_sha1 = manifest.get("prepared_git_blob_sha1")
    lookup.source_sha256 = manifest["prepared_sha256"]
    return lookup
