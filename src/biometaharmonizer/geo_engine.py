import logging
import re

import numpy as np
import pandas as pd
import pycountry


logger = logging.getLogger(__name__)


class GeoEngine:
    """
    Module 4: Geospatial Resolution Engine.
    Parses NCBI-style geo_loc_name strings into
    separate geo_country, geo_region, and geo_locality columns.
    Expected input format: 'Country: Region, Locality'
    Fallback format (no colon): 'Country, Locality'

    Output columns
    --------------
    geo_country   : normalised country display name (str or NaN)
    geo_region    : sub-national region as submitted (str or NaN)
    geo_locality  : locality / sub-region as submitted (str or NaN)
    geo_iso3166   : ISO 3166-1 alpha-2 country code (str or NaN)
    geo_sea_ocean : ocean/sea name for marine samples (str or NaN)
    geo_loc_raw   : original submitted string, present only when the value
                    could not be fully parsed (coordinate-only entries).
                    For all successfully parsed country entries this field
                    is NaN; the original value is always available in the
                    source geo_loc_name column.

    Note on geo_loc_raw:
      This column is intentionally restricted to coordinate-only and
      genuinely unparseable entries.  For normal country/region/locality
      strings the raw value is NOT duplicated here because geo_loc_name
      already holds it.  Populating geo_loc_raw for every row would triple-
      store the same string alongside geo_loc_name and this column.

    Note on coordinate-only entries:
      Records whose geo_loc_name contains only lat/lon coordinates (e.g.
      '40.71 N, 74.00 W') cannot be split into country/region/locality.
      They are stored in geo_loc_raw for downstream reverse-geocoding.
      geo_country and related fields are left NaN for these rows.
    """

    NULL_PATTERNS = re.compile(
        r"^(missing|unknown|n/?a|not provided|not collected|not applicable|na|none|--)$",
        re.IGNORECASE
    )

    _UK_SUBCOUNTRY = {
        "england": "GB",
        "scotland": "GB",
        "wales": "GB",
        "northern ireland": "GB",
    }

    _OCEAN_SEA = {
        "pacific ocean", "atlantic ocean", "indian ocean",
        "arctic ocean", "southern ocean", "mediterranean sea",
        "north sea", "caribbean sea", "south china sea",
        "baltic sea", "red sea", "black sea", "caspian sea",
        "arabian sea", "coral sea", "tasman sea",
    }

    _COORD_RE = re.compile(
        r"^[+-]?\d+\.?\d*\s*[NSEW]?\s*[,;\s]+\s*[+-]?\d+\.?\d*\s*[NSEW]?$",
        re.IGNORECASE,
    )

    def parse(self, series):
        results = series.apply(self._parse_single)
        return pd.DataFrame(results.tolist(), index=series.index)

    def _parse_single(self, value):
        empty = {
            "geo_country": np.nan, "geo_region": np.nan,
            "geo_locality": np.nan, "geo_iso3166": np.nan,
            "geo_sea_ocean": np.nan, "geo_loc_raw": np.nan,
        }
        if pd.isna(value):
            return empty
        value = str(value).strip()
        if self.NULL_PATTERNS.match(value):
            return empty

        # Coordinate-only: store raw for downstream reverse-geocoding;
        # do NOT populate country/region fields.
        if self._COORD_RE.match(value):
            result = dict(empty)
            result["geo_loc_raw"] = value
            return result

        country_str, region_str, locality_str = self._split_geo_string(value)

        # Ocean/sea detection (check the country part)
        if country_str.lower() in self._OCEAN_SEA:
            result = dict(empty)
            result["geo_sea_ocean"] = country_str
            return result

        # UK sub-country: normalise display name
        lower_country = country_str.lower().strip()
        if lower_country in self._UK_SUBCOUNTRY:
            display_country = "United Kingdom"
        else:
            display_country = country_str

        iso_code = self._resolve_iso(country_str)

        # geo_loc_raw is NaN for successfully parsed entries; the original
        # value is preserved in the source geo_loc_name column.
        return {
            "geo_country": display_country if country_str else np.nan,
            "geo_region": region_str if region_str else np.nan,
            "geo_locality": locality_str if locality_str else np.nan,
            "geo_iso3166": iso_code,
            "geo_sea_ocean": np.nan,
            "geo_loc_raw": np.nan,
        }

    def _split_geo_string(self, value):
        """
        Split an NCBI geo_loc_name string into (country, region, locality).

        Parsing rules (in order):
          1. 'Country: Region, Locality'  -> colon separates country; comma
             separates region and locality within the remainder.
          2. 'Country: Region'            -> colon present, no comma.
          3. 'Country, Locality'          -> no colon; comma separates country
             and locality (region left empty).
          4. 'Country'                    -> no colon, no comma.
        """
        country_str = region_str = locality_str = ""
        if ":" in value:
            parts = value.split(":", 1)
            country_str = parts[0].strip()
            remainder = parts[1].strip()
            if "," in remainder:
                sub = remainder.split(",", 1)
                region_str = sub[0].strip()
                locality_str = sub[1].strip()
            else:
                region_str = remainder
        else:
            if "," in value:
                parts = value.split(",", 1)
                country_str = parts[0].strip()
                locality_str = parts[1].strip()
            else:
                country_str = value
        return country_str, region_str, locality_str

    def _resolve_iso(self, country_str):
        if not country_str:
            return np.nan

        lower = country_str.lower().strip()

        if lower in self._UK_SUBCOUNTRY:
            return self._UK_SUBCOUNTRY[lower]

        if lower == "korea":
            logger.warning(
                "Ambiguous 'Korea' -- defaulting to South Korea (KR). "
                "Use 'North Korea' or 'South Korea' for precision."
            )
            return "KR"

        if lower in ("taiwan", "taiwan, province of china"):
            return "TW"

        if len(lower) < 3:
            logger.warning(
                "Country string too short for reliable ISO lookup: '%s'", country_str
            )
            return np.nan

        try:
            result = pycountry.countries.search_fuzzy(country_str)
            return result[0].alpha_2
        except Exception:
            return np.nan
