"""Optional, offline metadata enrichment without changing native output fields.

postprocess(df) returns (extended_df, diagnostics). Decimal coordinate pairs are
accepted in latitude/longitude order, either signed or with N/S and E/W suffixes.
Separate ENA coordinate attributes are also supported. No datum, location, or
jurisdiction is inferred. Conflicting complete sources remain unresolved.
"""

import json
import math
import re

import pandas as pd

__all__ = ["postprocess"]

_OUTPUT_COLUMNS = ("decimalLatitude", "decimalLongitude", "host_health_state")
_DIAGNOSTIC_COLUMNS = (
    "row_position", "biosample_accession", "coordinate_source",
    "coordinate_status", "issue",
)
_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
_COMPONENT = re.compile(rf"^({_NUMBER})\s*([NSEW])?$", re.IGNORECASE)
_DIRECTIONAL_PAIR = re.compile(
    rf"^({_NUMBER}\s*[NS])\s*[,;/]?\s*({_NUMBER}\s*[EW])$", re.IGNORECASE
)
_SIGNED_PAIR = re.compile(rf"^({_NUMBER})(?:\s*[,;/]\s*|\s+)({_NUMBER})$")
_NULL = re.compile(
    r"^(?:n/?a|none|null|nil|nan|missing(?:\s*:.*)?|unknown|"
    r"not\s+(?:provided|collected|applicable|available|reported)|[-.]+)$",
    re.IGNORECASE,
)


def _key(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


_LAT_KEYS = frozenset(map(_key, (
    "geographic location (latitude)", "latitude", "decimalLatitude",
    "decimal_latitude", "lat",
)))
_LON_KEYS = frozenset(map(_key, (
    "geographic location (longitude)", "longitude", "decimalLongitude",
    "decimal_longitude", "lon", "long",
)))
_HEALTH_KEYS = frozenset(map(_key, (
    "host_health_state", "host health state", "health_state", "health status",
)))


def _missing(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or bool(_NULL.fullmatch(value.strip()))
    if isinstance(value, (dict, list, tuple, set)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _extras(value):
    if _missing(value):
        return {}, None
    if isinstance(value, dict):
        return value, None
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return {}, "invalid_extra_attributes_json"
        if isinstance(decoded, dict):
            return decoded, None
    return {}, "extra_attributes_not_an_object"


def _component(value, axis):
    if _missing(value):
        return None, "missing"
    if isinstance(value, bool) or not pd.api.types.is_scalar(value):
        return None, "invalid"
    text = str(value).strip()
    match = _COMPONENT.fullmatch(text)
    if match is None:
        return None, "unsupported"
    number, hemisphere = match.groups()
    coordinate = float(number)
    if hemisphere:
        hemisphere = hemisphere.upper()
        if hemisphere not in ("NS" if axis == "latitude" else "EW"):
            return None, "invalid"
        negative = hemisphere in "SW"
        if (number.startswith("-") and not negative) or (number.startswith("+") and negative):
            return None, "invalid"
        coordinate = -abs(coordinate) if negative else abs(coordinate)
    limit = 90 if axis == "latitude" else 180
    if not math.isfinite(coordinate) or not -limit <= coordinate <= limit:
        return None, "invalid"
    return coordinate, "valid"


def _combined(value):
    if _missing(value):
        return None, "missing"
    if not isinstance(value, str):
        return None, "unsupported"
    text = value.strip()
    match = _DIRECTIONAL_PAIR.fullmatch(text) or _SIGNED_PAIR.fullmatch(text)
    if match is None:
        return None, "unsupported"
    latitude, lat_status = _component(match.group(1), "latitude")
    longitude, lon_status = _component(match.group(2), "longitude")
    if lat_status != "valid" or lon_status != "valid":
        return None, "invalid"
    return (latitude, longitude), "valid"


def _axis(extras, aliases, axis):
    candidates = [v for k, v in extras.items() if _key(k) in aliases and not _missing(v)]
    if not candidates:
        return None, "missing"
    parsed = [_component(value, axis) for value in candidates]
    if any(status != "valid" for _, status in parsed):
        return None, "invalid"
    values = [value for value, _ in parsed]
    if any(not math.isclose(values[0], value, abs_tol=1e-8, rel_tol=0) for value in values[1:]):
        return None, "conflict"
    return values[0], "valid"


def _separate(extras):
    latitude, lat_status = _axis(extras, _LAT_KEYS, "latitude")
    longitude, lon_status = _axis(extras, _LON_KEYS, "longitude")
    statuses = (lat_status, lon_status)
    if statuses == ("missing", "missing"):
        return None, "missing"
    if "conflict" in statuses:
        return None, "conflict"
    if "invalid" in statuses:
        return None, "invalid"
    if "missing" in statuses:
        return None, "incomplete"
    return (latitude, longitude), "valid"


def _health(extras):
    values = [v for k, v in extras.items() if _key(k) in _HEALTH_KEYS and not _missing(v)]
    if not values:
        return None, None
    if any(not isinstance(v, str) for v in values):
        return None, "invalid_host_health_state"
    if len({v.strip().casefold() for v in values}) != 1:
        return None, "conflicting_host_health_state"
    return values[0], None


def postprocess(df):
    """Append three fields and return row-level diagnostics, without I/O.

    Original cells, row order, index, and columns remain unchanged. Existing
    derived output columns are rejected rather than overwritten. Coordinates
    must form a complete valid pair. Agreement uses an absolute tolerance of
    1e-8 degrees per component with no relative tolerance; this is a numeric
    comparison, not a spatial verification. Unsupported formats, malformed
    overflow JSON, and conflicting sources are recorded in diagnostics.
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError("postprocess expects a pandas DataFrame")
    if not df.columns.is_unique:
        raise ValueError("postprocess requires unique column names")
    collisions = [column for column in _OUTPUT_COLUMNS if column in df.columns]
    if collisions:
        raise ValueError("Post-processing output columns already exist: " + ", ".join(collisions))

    out = df.copy(deep=True)
    latitudes, longitudes, health_states, reports = [], [], [], []
    columns = list(df.columns)
    positions = {name: pos for pos, name in enumerate(columns)}
    for position, values in enumerate(df.itertuples(index=False, name=None)):
        def cell(name):
            return values[positions[name]] if name in positions else None

        extras, extras_issue = _extras(cell("_extra_attributes"))
        combined, combined_status = _combined(cell("lat_lon"))
        separate, separate_status = _separate(extras)
        issues = [extras_issue] if extras_issue else []
        if combined_status not in ("valid", "missing"):
            issues.append("lat_lon_" + combined_status)
        if separate_status not in ("valid", "missing"):
            issues.append("separate_coordinates_" + separate_status)
        pair, source = None, None
        if separate_status == "conflict":
            status = "conflict"
        elif combined is not None and separate is not None:
            if all(math.isclose(a, b, abs_tol=1e-8, rel_tol=0) for a, b in zip(combined, separate)):
                pair, source, status = combined, "both", "agreement"
            else:
                status = "conflict"
                issues.append("coordinate_sources_disagree")
        elif combined is not None:
            pair, source, status = combined, "lat_lon", "parsed"
        elif separate is not None:
            pair, source, status = separate, "extra_attributes", "recovered"
        elif separate_status == "invalid" or combined_status == "invalid":
            status = "invalid"
        elif separate_status == "incomplete":
            status = "incomplete"
        elif combined_status == "unsupported":
            status = "unsupported"
        else:
            status = "missing"
        health, health_issue = _health(extras)
        if health_issue:
            issues.append(health_issue)
        latitudes.append(pair[0] if pair is not None else float("nan"))
        longitudes.append(pair[1] if pair is not None else float("nan"))
        health_states.append(health)
        reports.append((position, cell("biosample_accession"), source, status, ";".join(issues)))

    out["decimalLatitude"] = pd.array(latitudes, dtype="float64")
    out["decimalLongitude"] = pd.array(longitudes, dtype="float64")
    out["host_health_state"] = pd.array(health_states, dtype="object")
    diagnostics = pd.DataFrame(reports, columns=_DIAGNOSTIC_COLUMNS)
    return out, diagnostics
