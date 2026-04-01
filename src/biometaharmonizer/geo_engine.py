import pandas as pd
import numpy as np
import pycountry
import re


class GeoEngine:
    """
    Module 4: Geospatial Resolution Engine.
    Parses NCBI-style geo_loc_name strings into
    separate Country, Region, and Locality columns.
    Expected input format: 'Country: Region, Locality'
    """

    NULL_PATTERNS = re.compile(
        r"^(missing|unknown|n/?a|not provided|not collected|not applicable|na|none|--)$",
        re.IGNORECASE
    )

    def parse(self, series):
        results = series.apply(self._parse_single)
        return pd.DataFrame(results.tolist(), index=series.index)

    def _parse_single(self, value):
        empty = {"Country": np.nan, "Region": np.nan, "Locality": np.nan, "ISO3166": np.nan}
        if pd.isna(value):
            return empty
        value = str(value).strip()
        if self.NULL_PATTERNS.match(value):
            return empty
        country_str, region_str, locality_str = self._split_geo_string(value)
        iso_code = self._resolve_iso(country_str)
        return {
            "Country": country_str if country_str else np.nan,
            "Region": region_str if region_str else np.nan,
            "Locality": locality_str if locality_str else np.nan,
            "ISO3166": iso_code
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
        try:
            result = pycountry.countries.search_fuzzy(country_str)
            return result[0].alpha_2
        except LookupError:
            return np.nan
