"""Offline country containment lookup for a caller-supplied GeoJSON layer.

This component is not yet connected to post-processing or the CLI. Reference
codes must be audited before use. No data is downloaded by this module.
"""

import functools
import hashlib
import json
import math
import re
from pathlib import Path
from typing import NamedTuple, Optional


class CountryMatch(NamedTuple):
    country: Optional[str]
    country_code: Optional[str]
    status: str


class CoordinateCountryLookup:
    """Query WGS84 polygon interiors; borders and ambiguous matches abstain.

    valid_countries maps accepted ISO alpha-2 codes to canonical country names.
    It should be built from the installed pycountry database by the caller.
    Country-code properties are explicit, not guessed from feature names.
    """

    @classmethod
    def from_geojson(cls, path, *, expected_git_blob_sha1, valid_countries,
                     country_code_field, crs):
        """Verify immutable file content, then build a reusable spatial index.

        crs must explicitly be EPSG:4326. This describes the polygon layer, not
        the certainty of any sample coordinate datum. No reprojection occurs.
        The Git blob checksum includes its size header, unlike ordinary SHA-1.
        """
        if crs != "EPSG:4326":
            raise ValueError("Country polygons must use EPSG:4326 longitude/latitude")
        if not isinstance(expected_git_blob_sha1, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_git_blob_sha1):
            raise ValueError("An expected lowercase Git blob SHA-1 is required")
        raw = Path(path).read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
        if actual != expected_git_blob_sha1:
            raise ValueError("Country reference checksum mismatch")
        data = json.loads(raw)
        result = cls(data, valid_countries=valid_countries, country_code_field=country_code_field)
        result.source_git_blob_sha1 = actual
        result.source_sha256 = hashlib.sha256(raw).hexdigest()
        return result

    def __init__(self, data, *, valid_countries, country_code_field):
        try:
            import shapely
            from shapely.geometry import Point, shape
            from shapely.strtree import STRtree
        except ImportError as exc:
            raise ImportError("Country lookup requires the optional biometaharmonizer[geo] dependency") from exc
        if int(shapely.__version__.split(".")[0]) < 2:
            raise ImportError("Country lookup requires Shapely 2 or newer")
        if not isinstance(valid_countries, dict) or not valid_countries:
            raise ValueError("Provide a nonempty ISO-code to canonical-country-name dictionary")
        if any(not isinstance(code, str) or not re.fullmatch(r"[A-Z]{2}", code)
               or not isinstance(name, str) or not name.strip()
               for code, name in valid_countries.items()):
            raise ValueError("Invalid country-code/name dictionary")
        if not isinstance(country_code_field, str) or not country_code_field:
            raise ValueError("An explicit reference country-code field is required")
        if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
            raise ValueError("Country reference must be a GeoJSON FeatureCollection")
        features = data.get("features")
        if not isinstance(features, list) or not features:
            raise ValueError("Country reference has no features")
        self._countries = dict(valid_countries)
        self._Point = Point
        self._geometries, self._codes = [], []
        self.unmapped_feature_indices = []
        for index, feature in enumerate(features):
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise ValueError(f"Invalid feature at index {index}")
            properties = feature.get("properties")
            if not isinstance(properties, dict) or country_code_field not in properties:
                raise ValueError(f"Missing country-code property in feature {index}")
            geometry = feature.get("geometry")
            if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon"):
                raise ValueError(f"Non-polygon geometry in feature {index}")
            self._validate_rings(geometry, index)
            polygon = shape(geometry)
            if polygon.is_empty or not polygon.is_valid:
                raise ValueError(f"Empty or invalid polygon in feature {index}; audit the reference")
            raw_code = properties[country_code_field]
            code = raw_code.strip().upper() if isinstance(raw_code, str) else None
            if code not in self._countries:
                code = None
                self.unmapped_feature_indices.append(index)
            self._geometries.append(polygon)
            self._codes.append(code)
        self.unmapped_feature_indices = tuple(self.unmapped_feature_indices)
        self._tree = STRtree(self._geometries)
        self._cached_lookup = functools.lru_cache(maxsize=8192)(self._lookup)
        self.source_git_blob_sha1 = None
        self.source_sha256 = None

    @staticmethod
    def _validate_rings(geometry, index):
        polygons = [geometry.get("coordinates")] if geometry["type"] == "Polygon" else geometry.get("coordinates")
        if not isinstance(polygons, list) or not polygons:
            raise ValueError(f"Missing polygon coordinates in feature {index}")
        for polygon in polygons:
            if not isinstance(polygon, list) or not polygon:
                raise ValueError(f"Missing polygon rings in feature {index}")
            for ring in polygon:
                if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                    raise ValueError(f"Unclosed or incomplete ring in feature {index}")
                for point in ring:
                    if not isinstance(point, (list, tuple)) or len(point) != 2:
                        raise ValueError(f"Expected two-dimensional coordinates in feature {index}")
                    x, y = point
                    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in point):
                        raise ValueError(f"Nonfinite or nonnumeric coordinates in feature {index}")
                    if not -180 <= x <= 180 or not -90 <= y <= 90:
                        raise ValueError(f"Out-of-range polygon coordinates in feature {index}")
                if any(abs(a[0] - b[0]) > 180 for a, b in zip(ring, ring[1:])):
                    raise ValueError(f"Unsplit antimeridian ring in feature {index}")

    def lookup(self, latitude, longitude):
        """Return a CountryMatch; never infer an ocean or the nearest country."""
        try:
            if isinstance(latitude, bool) or isinstance(longitude, bool):
                raise ValueError("Boolean coordinate")
            latitude, longitude = float(latitude), float(longitude)
            if not math.isfinite(latitude) or not math.isfinite(longitude):
                raise ValueError("Nonfinite coordinate")
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                raise ValueError("Out-of-range coordinate")
        except (ValueError, TypeError, OverflowError):
            return CountryMatch(None, None, "invalid_coordinates")
        return self._cached_lookup(latitude, longitude)

    def _lookup(self, latitude, longitude):
        point = self._Point(longitude, latitude)
        hits = self._tree.query(point, predicate="intersects")
        if len(hits) == 0:
            return CountryMatch(None, None, "no_polygon_match")
        if any(not self._geometries[int(i)].contains(point) for i in hits):
            return CountryMatch(None, None, "boundary_or_ambiguous")
        codes = {self._codes[int(i)] for i in hits}
        if None in codes:
            return CountryMatch(None, None, "unmapped_reference_code")
        if len(codes) != 1:
            return CountryMatch(None, None, "boundary_or_ambiguous")
        code = next(iter(codes))
        return CountryMatch(self._countries[code], code, "matched")
