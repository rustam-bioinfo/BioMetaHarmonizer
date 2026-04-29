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
    geo_iso3166   : ISO 3166-1 alpha-2 country code (str or 'HISTORICAL' or NaN)
    geo_sea_ocean : ocean/sea name for marine samples (str or NaN)
    geo_loc_raw   : original submitted string, present only when the value
                    could not be fully parsed (coordinate-only entries).
                    For all successfully parsed country entries this field
                    is NaN; the original value is always available in the
                    source geo_loc_name column.
    """

    NULL_PATTERNS = re.compile(
        r"^(?:-+|\.+|n/?a|na|nd|nr|ns|nt|none|null|nil|"
        r"missing|misssing|missng|mising|"
        r"unknown|unkown|unknwon|unknow|"
        r"not\s+provided|not\s+collected|not\s+applicable|not\s+available|"
        r"not\s+determined|not\s+recorded|not\s+reported|not\s+known|"
        r"not\s+given|not\s+stated|not\s+specified|"
        r"not\s+done|not\s+tested|not\s+sequenced|not\s+typed|"
        r"unavailable|unspecified|undetermined|unidentified|"
        r"restricted|restricted\s+access|withheld|confidential|"
        r"tbd|tba|"
        r"missing\s*:.*|not\s+applicable\s*:.*|data\s+agreement\s+established\s+pre-?2023)$",
        re.IGNORECASE,
    )

    _UK_SUBCOUNTRY = {
        "england": "GB",
        "scotland": "GB",
        "wales": "GB",
        "northern ireland": "GB",
    }

    _COUNTRY_ALIASES = {
        "turkey": "TR",
        "türkiye": "TR",
        "namibia": "NA",
        "democratic republic of the congo": "CD",
        "dr congo": "CD",
        "drc": "CD",
        "congo-kinshasa": "CD",
        "burma": "MM",
        "myanmar (burma)": "MM",
    }

    _HISTORICAL_COUNTRIES = {
        "ussr",
        "soviet union",
        "union of soviet socialist republics",
        "yugoslavia",
        "sfr yugoslavia",
        "socialist federal republic of yugoslavia",
        "czechoslovakia",
        "cssr",
        "czechoslovakia",
        "german democratic republic",
        "east germany",
        "west germany",
        "federal republic of germany",
        "north vietnam",
        "south vietnam",
        "north yemen",
        "south yemen",
        "zaire",
        "byelorussia",
        "byelorussian ssr",
        "serbia and montenegro",
        "netherlands antilles",
    }

    _OCEAN_SEA = {
        "pacific ocean", "atlantic ocean", "indian ocean",
        "arctic ocean", "southern ocean", "mediterranean sea",
        "north sea", "caribbean sea", "south china sea",
        "baltic sea", "red sea", "black sea", "caspian sea",
        "arabian sea", "coral sea", "tasman sea",
    }

    _COORD_RE = re.compile(
        r"^[+-]?\d+\.?\d*\s*[NSns]?\s*[,;\s]+\s*[+-]?\d+\.?\d*\s*[EWew]?$"
    )

    def _empty(self):
        return {
            "geo_country": np.nan, "geo_region": np.nan,
            "geo_locality": np.nan, "geo_iso3166": np.nan,
            "geo_sea_ocean": np.nan, "geo_loc_raw": np.nan,
        }

    def parse(self, series):
        empty = self._empty()

        unique_vals = series.dropna().unique()
        logger.debug(
            "GeoEngine: %d rows, %d unique non-null values to resolve.",
            len(series), len(unique_vals),
        )

        cache = {v: self._parse_single(v) for v in unique_vals}

        results = [
            cache[v] if pd.notna(v) and v in cache else empty
            for v in series
        ]
        return pd.DataFrame(results, index=series.index)

    def _parse_single(self, value):
        empty = self._empty()
        if pd.isna(value):
            return empty

        value = str(value).strip()
        if self.NULL_PATTERNS.match(value):
            return empty

        if self._COORD_RE.match(value):
            result = dict(empty)
            result["geo_loc_raw"] = value
            return result

        country_str, region_str, locality_str = self._split_geo_string(value)

        if country_str.lower() in self._OCEAN_SEA:
            result = dict(empty)
            result["geo_sea_ocean"] = country_str
            if region_str:
                result["geo_locality"] = region_str if not locality_str else f"{region_str}, {locality_str}"
            return result

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
            logger.info(
                "'Korea' without qualifier resolved to South Korea (KR). "
                "Use 'North Korea' for DPRK."
            )
            return "KR"

        if lower in ("taiwan", "taiwan, province of china"):
            return "TW"

        if lower in self._COUNTRY_ALIASES:
            return self._COUNTRY_ALIASES[lower]

        if lower in self._HISTORICAL_COUNTRIES:
            logger.warning(
                "Historical/defunct country name '%s' detected -- tagging geo_iso3166 as HISTORICAL.",
                country_str,
            )
            return "HISTORICAL"

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
