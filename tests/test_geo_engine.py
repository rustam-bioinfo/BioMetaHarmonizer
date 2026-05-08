"""
Tests for geo_engine.py.

All tests use synthetic data -- no live NCBI calls are made.
"""

import numpy as np
import pandas as pd
import pytest

from biometaharmonizer.geo_engine import GeoEngine


@pytest.fixture(scope="module")
def ge():
    return GeoEngine()


def _parse(ge, value):
    """Helper: parse a single geo_loc_name string, return the first result row."""
    result = ge.parse(pd.Series([value]))
    return result.iloc[0]


class TestCountryColonRegionLocality:
    def test_full_three_part(self, ge):
        row = _parse(ge, "Russia: Moscow, Butovo")
        assert row["geo_country"] == "Russia"
        assert row["geo_region"] == "Moscow"
        assert row["geo_locality"] == "Butovo"
        assert row["geo_iso3166"] == "RU"
        assert pd.isna(row["geo_sea_ocean"])

    def test_country_colon_region_only(self, ge):
        row = _parse(ge, "France: Ile-de-France")
        assert row["geo_country"] == "France"
        assert row["geo_region"] == "Ile-de-France"
        assert pd.isna(row["geo_locality"])

    def test_country_only(self, ge):
        row = _parse(ge, "Germany")
        assert row["geo_country"] == "Germany"
        assert row["geo_iso3166"] == "DE"
        assert pd.isna(row["geo_region"])
        assert pd.isna(row["geo_locality"])


class TestFallbackNoColon:
    def test_country_comma_locality_fallback(self, ge):
        row = _parse(ge, "Brazil, São Paulo")
        assert row["geo_country"] == "Brazil"
        assert row["geo_locality"] == "São Paulo"


class TestNullValues:
    @pytest.mark.parametrize("raw", [
        "missing", "unknown", "not provided", "NA", "N/A", "none", "null",
    ])
    def test_null_returns_all_nan(self, ge, raw):
        row = _parse(ge, raw)
        assert pd.isna(row["geo_country"])
        assert pd.isna(row["geo_iso3166"])


class TestOceanSea:
    @pytest.mark.parametrize("ocean", [
        "Pacific Ocean",
        "Mediterranean Sea",
        "Baltic Sea",
        "Persian Gulf",
        "Bay of Bengal",
    ])
    def test_ocean_routed_to_geo_sea_ocean(self, ge, ocean):
        row = _parse(ge, ocean)
        assert row["geo_sea_ocean"] == ocean
        assert pd.isna(row["geo_country"])
        assert pd.isna(row["geo_iso3166"])

    def test_ocean_with_locality(self, ge):
        row = _parse(ge, "Atlantic Ocean: offshore station 12")
        assert row["geo_sea_ocean"] == "Atlantic Ocean"
        assert row["geo_locality"] == "offshore station 12"


class TestIsoResolution:
    @pytest.mark.parametrize("country,expected_iso", [
        ("United States", "US"),
        ("China",         "CN"),
        ("India",         "IN"),
        ("Turkey",        "TR"),
        ("Türkiye",       "TR"),
        ("Namibia",       "NA"),
        ("England",       "GB"),
        ("Scotland",      "GB"),
        ("Wales",         "GB"),
        ("Taiwan",        "TW"),
        ("Korea",         "KR"),
    ])
    def test_iso_resolution(self, ge, country):
        row = _parse(ge, country)
        assert row["geo_iso3166"] == expected_iso

    def test_united_kingdom_display_name_for_england(self, ge):
        row = _parse(ge, "England")
        assert row["geo_country"] == "United Kingdom"

    @pytest.mark.parametrize("historical", [
        "USSR",
        "Yugoslavia",
        "East Germany",
        "Czechoslovakia",
        "Zaire",
    ])
    def test_historical_countries_tagged(self, ge, historical):
        row = _parse(ge, historical)
        assert row["geo_iso3166"] == "HISTORICAL"


class TestCoordinateEntries:
    def test_coordinate_string_stored_in_geo_loc_raw(self, ge):
        row = _parse(ge, "55.7558 N, 37.6173 E")
        assert pd.notna(row["geo_loc_raw"])
        assert pd.isna(row["geo_country"])


class TestNaNInput:
    def test_nan_row_returns_all_nan(self, ge):
        result = ge.parse(pd.Series([np.nan]))
        row = result.iloc[0]
        assert pd.isna(row["geo_country"])
        assert pd.isna(row["geo_iso3166"])


class TestDeduplication:
    def test_repeated_country_parsed_correctly(self, ge):
        s = pd.Series(["Russia", "Russia", "Russia"])
        result = ge.parse(s)
        assert list(result["geo_iso3166"]) == ["RU", "RU", "RU"]


class TestParenthesisStripping:
    def test_trailing_paren_stripped_from_country(self, ge):
        row = _parse(ge, "Russia (European part)")
        assert row["geo_iso3166"] == "RU"
