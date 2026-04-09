import logging
import pandas as pd
import numpy as np
import pycountry
import re


logger = logging.getLogger(__name__)


class GeoEngine:
    """
    Module 4: Geospatial Resolution Engine.
    Parses NCBI-style geo_loc_name strings into
    separate geo_country, geo_region, and geo_locality columns.
    Expected input format: 'Country: Region, Locality'
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

        # Coordinate detection
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

        # UK sub-country: override country name
        lower_country = country_str.lower().strip()
        if lower_country in self._UK_SUBCOUNTRY:
            display_country = "United Kingdom"
        else:
            display_country = country_str

        iso_code = self._resolve_iso(country_str)
        return {
            "geo_country": display_country if country_str else np.nan,
            "geo_region": region_str if region_str else np.nan,
            "geo_locality": locality_str if locality_str else np.nan,
            "geo_iso3166": iso_code,
            "geo_sea_ocean": np.nan,
            "geo_loc_raw": np.nan,
        }

    def _split_geo_string(self, value):
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
            country_str = value
        return country_str, region_str, locality_str

    def _resolve_iso(self, country_str):
        if not country_str:
            return np.nan

        lower = country_str.lower().strip()

        # UK sub-country lookup
        if lower in self._UK_SUBCOUNTRY:
            return self._UK_SUBCOUNTRY[lower]

        # Korea ambiguity
        if lower == "korea":
            logger.warning(
                "Ambiguous 'Korea' — defaulting to South Korea (KR). "
                "Use 'North Korea' or 'South Korea' for precision."
            )
            return "KR"

        # Taiwan explicit lookup
        if lower in ("taiwan", "taiwan, province of china"):
            return "TW"

        try:
            result = pycountry.countries.search_fuzzy(country_str)
            return result[0].alpha_2
        except LookupError:
            return np.nan
