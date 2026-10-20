"""Conservative coordinate-derived country fallback for extended output only."""

import logging
import math

import pandas as pd

logger = logging.getLogger(__name__)

_COLUMNS = (
    "coordinate_country", "coordinate_country_code", "country_lookup_status",
    "country_assignment_source", "country_assignment_status", "coordinate_datum_status",
)
_DATUM_KEYS = {"geodetic datum", "geodeticdatum", "coordinate datum", "coordinate reference system", "crs"}
_WGS84 = {"wgs84", "wgs 84", "epsg 4326", "ogc crs84", "crs84", "urn ogc def crs ogc 1 3 crs84"}

def apply_country_fallback(out, diagnostics, *, lookup=None):
    """Fill only absent derived country pairs, with positional diagnostics.

    Unknown sample datum is explicitly assumed WGS84. Unsupported explicit
    datums are not transformed. Native geography is never overwritten. Existing
    text assignments, partial assignments, and HISTORICAL/XK markers are retained.
    """
    from biometaharmonizer.postprocess import _extras, _key, _missing, _ISO_ALPHA2

    if len(out) != len(diagnostics):
        raise ValueError("Country diagnostics must match output row count")
    country_values = out["country"].tolist()
    code_values = out["countryCode"].tolist()
    positions = {name: i for i, name in enumerate(out.columns)}
    report = []
    for row_position, values in enumerate(out.itertuples(index=False, name=None)):
        def cell(name):
            return values[positions[name]] if name in positions else None

        country, code = country_values[row_position], code_values[row_position]
        country_present = not _missing(country)
        code_present = isinstance(code, str) and code in _ISO_ALPHA2
        assignment_source = "text" if country_present or code_present else "unresolved"
        assignment_status = "text_retained" if assignment_source == "text" else "unresolved"
        datum_status = "not_evaluated"
        candidate_country = candidate_code = None
        lookup_status = "not_attempted"
        marker = cell("geo_iso3166")
        if isinstance(marker, str) and marker.strip().upper() in {"HISTORICAL", "XK"}:
            report.append((None, None, "not_attempted", "text", "historical_or_special_retained", datum_status))
            continue
        latitude, longitude = cell("decimalLatitude"), cell("decimalLongitude")
        if _missing(latitude) or _missing(longitude) or not math.isfinite(float(latitude)) or not math.isfinite(float(longitude)):
            report.append((None, None, "coordinates_unavailable", assignment_source, assignment_status, datum_status))
            continue
        extras, _ = _extras(cell("_extra_attributes"))
        datum_values = [value for key, value in extras.items() if _key(key) in _DATUM_KEYS and not _missing(value)]
        datum_values += [cell(name) for name in out.columns if _key(name) in _DATUM_KEYS and not _missing(cell(name))]
        if not datum_values:
            datum_status = "assumed_wgs84"
        elif all(isinstance(value, str) and _key(value) in _WGS84 for value in datum_values):
            datum_status = "declared_wgs84"
        else:
            datum_status = "unsupported_or_conflicting_datum"
            report.append((None, None, "unsupported_datum", assignment_source, assignment_status, datum_status))
            continue
        if lookup is None:
            from biometaharmonizer.country_reference import load_bundled_country_lookup
            lookup = load_bundled_country_lookup()
        match = lookup.lookup(latitude, longitude)
        lookup_status = match.status
        candidate_country, candidate_code = match.country, match.country_code
        if lookup_status == "matched":
            if not isinstance(candidate_country, str) or not candidate_country.strip() or candidate_code not in _ISO_ALPHA2:
                lookup_status = "unmapped_reference_code"
            elif country_present or code_present:
                if code_present:
                    assignment_status = "text_agrees_with_coordinates" if code == candidate_code else "text_coordinate_disagreement"
                else:
                    assignment_status = "partial_text_assignment_retained"
            else:
                country_values[row_position] = candidate_country
                code_values[row_position] = candidate_code
                assignment_source, assignment_status = "coordinates", "filled_from_coordinates"
        report.append((candidate_country, candidate_code, lookup_status, assignment_source, assignment_status, datum_status))
    out["country"] = pd.array(country_values, dtype="object")
    out["countryCode"] = pd.array(code_values, dtype="object")
    for i, column in enumerate(_COLUMNS):
        diagnostics[column] = pd.array([row[i] for row in report], dtype="object")
    logger.info("Country assignment status counts: %s", diagnostics["country_assignment_status"].value_counts().to_dict())
    return out, diagnostics
