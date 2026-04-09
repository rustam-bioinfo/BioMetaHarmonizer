import pytest
import numpy as np
import pandas as pd
from biometaharmonizer.geo_engine import GeoEngine


@pytest.fixture
def engine():
    return GeoEngine()


# --- Country-only (real NCBI data) ---

class TestCountryOnly:

    def test_brazil(self, engine):
        s = pd.Series(["Brazil"])
        result = engine.parse(s)
        assert result["geo_country"][0] == "Brazil"
        assert result["geo_iso3166"][0] == "BR"
        assert pd.isna(result["geo_region"][0])
        assert pd.isna(result["geo_locality"][0])

    def test_united_kingdom(self, engine):
        s = pd.Series(["United Kingdom"])
        result = engine.parse(s)
        assert result["geo_country"][0] == "United Kingdom"
        assert result["geo_iso3166"][0] == "GB"

    def test_ethiopia(self, engine):
        s = pd.Series(["Ethiopia"])
        result = engine.parse(s)
        assert result["geo_country"][0] == "Ethiopia"
        assert result["geo_iso3166"][0] == "ET"

    def test_russia(self, engine):
        s = pd.Series(["Russia"])
        result = engine.parse(s)
        assert result["geo_iso3166"][0] == "RU"


# --- Country: Region format ---

class TestCountryRegion:

    def test_peru_lima(self, engine):
        s = pd.Series(["Peru: Lima"])
        result = engine.parse(s)
        assert result["geo_country"][0] == "Peru"
        assert result["geo_region"][0] == "Lima"
        assert result["geo_iso3166"][0] == "PE"
        assert pd.isna(result["geo_locality"][0])

    def test_usa_california(self, engine):
        s = pd.Series(["USA: California"])
        result = engine.parse(s)
        assert result["geo_country"][0] == "USA"
        assert result["geo_region"][0] == "California"
        assert result["geo_iso3166"][0] == "US"

    def test_russia_moscow(self, engine):
        s = pd.Series(["Russia: Moscow"])
        result = engine.parse(s)
        assert result["geo_country"][0] == "Russia"
        assert result["geo_region"][0] == "Moscow"
        assert result["geo_iso3166"][0] == "RU"


# --- Country: Region, Locality format ---

class TestCountryRegionLocality:

    def test_mexico_calnali_hidalgo(self, engine):
        s = pd.Series(["Mexico: Calnali, Hidalgo"])
        result = engine.parse(s)
        assert result["geo_country"][0] == "Mexico"
        assert result["geo_region"][0] == "Calnali"
        assert result["geo_locality"][0] == "Hidalgo"
        assert result["geo_iso3166"][0] == "MX"

    def test_russia_moscow_hospital(self, engine):
        s = pd.Series(["Russia: Moscow, hospital 4"])
        result = engine.parse(s)
        assert result["geo_country"][0] == "Russia"
        assert result["geo_region"][0] == "Moscow"
        assert result["geo_locality"][0] == "hospital 4"

    def test_china_guangdong_shenzhen(self, engine):
        s = pd.Series(["China: Guangdong, Shenzhen"])
        result = engine.parse(s)
        assert result["geo_country"][0] == "China"
        assert result["geo_region"][0] == "Guangdong"
        assert result["geo_locality"][0] == "Shenzhen"
        assert result["geo_iso3166"][0] == "CN"


# --- UK sub-country ---

class TestUKSubCountry:

    def test_england_resolves_to_gb(self, engine):
        s = pd.Series(["England"])
        result = engine.parse(s)
        assert result["geo_country"][0] == "United Kingdom"
        assert result["geo_iso3166"][0] == "GB"

    def test_scotland_resolves_to_gb(self, engine):
        s = pd.Series(["Scotland"])
        result = engine.parse(s)
        assert result["geo_country"][0] == "United Kingdom"
        assert result["geo_iso3166"][0] == "GB"

    def test_wales_resolves_to_gb(self, engine):
        s = pd.Series(["Wales"])
        result = engine.parse(s)
        assert result["geo_country"][0] == "United Kingdom"
        assert result["geo_iso3166"][0] == "GB"

    def test_northern_ireland_resolves_to_gb(self, engine):
        s = pd.Series(["Northern Ireland"])
        result = engine.parse(s)
        assert result["geo_country"][0] == "United Kingdom"
        assert result["geo_iso3166"][0] == "GB"


# --- Korea ambiguity ---

class TestKoreaAmbiguity:

    def test_korea_defaults_to_kr(self, engine):
        s = pd.Series(["Korea"])
        result = engine.parse(s)
        assert result["geo_iso3166"][0] == "KR"


# --- Taiwan ---

class TestTaiwan:

    def test_taiwan_resolves_to_tw(self, engine):
        s = pd.Series(["Taiwan"])
        result = engine.parse(s)
        assert result["geo_iso3166"][0] == "TW"


# --- Ocean/sea names ---

class TestOceanSea:

    def test_pacific_ocean(self, engine):
        s = pd.Series(["Pacific Ocean"])
        result = engine.parse(s)
        assert pd.isna(result["geo_country"][0])
        assert result["geo_sea_ocean"][0] == "Pacific Ocean"

    def test_mediterranean_sea(self, engine):
        s = pd.Series(["Mediterranean Sea"])
        result = engine.parse(s)
        assert pd.isna(result["geo_country"][0])
        assert result["geo_sea_ocean"][0] == "Mediterranean Sea"

    def test_north_sea(self, engine):
        s = pd.Series(["North Sea"])
        result = engine.parse(s)
        assert pd.isna(result["geo_country"][0])
        assert result["geo_sea_ocean"][0] == "North Sea"


# --- Coordinate detection ---

class TestCoordinateDetection:

    def test_lat_lon_nsew(self, engine):
        s = pd.Series(["39.9N 116.4E"])
        result = engine.parse(s)
        assert pd.isna(result["geo_country"][0])
        assert result["geo_loc_raw"][0] == "39.9N 116.4E"

    def test_decimal_degrees(self, engine):
        s = pd.Series(["51.5 -0.1"])
        result = engine.parse(s)
        assert pd.isna(result["geo_country"][0])
        assert result["geo_loc_raw"][0] == "51.5 -0.1"


# --- Null string guard (real NCBI values) ---

class TestNullStrings:

    def test_not_collected(self, engine):
        s = pd.Series(["not collected"])
        result = engine.parse(s)
        assert pd.isna(result["geo_country"][0])
        assert pd.isna(result["geo_iso3166"][0])

    def test_not_applicable(self, engine):
        s = pd.Series(["not applicable"])
        result = engine.parse(s)
        assert pd.isna(result["geo_country"][0])

    def test_missing_string(self, engine):
        s = pd.Series(["missing"])
        result = engine.parse(s)
        assert pd.isna(result["geo_country"][0])

    def test_unknown_string(self, engine):
        s = pd.Series(["unknown"])
        result = engine.parse(s)
        assert pd.isna(result["geo_country"][0])


# --- Null and edge cases ---

class TestNullAndEdgeCases:

    def test_nan_input_returns_nan_row(self, engine):
        s = pd.Series([np.nan])
        result = engine.parse(s)
        assert pd.isna(result["geo_country"][0])
        assert pd.isna(result["geo_iso3166"][0])

    def test_unresolvable_country_iso_is_nan(self, engine):
        s = pd.Series(["Narnia"])
        result = engine.parse(s)
        assert result["geo_country"][0] == "Narnia"
        assert pd.isna(result["geo_iso3166"][0])

    def test_whitespace_stripped(self, engine):
        s = pd.Series(["  Russia: Moscow  "])
        result = engine.parse(s)
        assert result["geo_country"][0] == "Russia"
        assert result["geo_region"][0] == "Moscow"

    def test_output_is_dataframe(self, engine):
        s = pd.Series(["Brazil", "Peru: Lima"])
        result = engine.parse(s)
        assert isinstance(result, pd.DataFrame)
        assert "geo_country" in result.columns
        assert "geo_region" in result.columns
        assert "geo_locality" in result.columns
        assert "geo_iso3166" in result.columns

    def test_index_preserved(self, engine):
        s = pd.Series(["Brazil", "Peru: Lima"], index=[10, 20])
        result = engine.parse(s)
        assert list(result.index) == [10, 20]


# --- Mixed real series ---

class TestMixedRealSeries:

    def test_full_bcereus_geo_column(self, engine):
        s = pd.Series([
            "Brazil", "Peru: Lima", "United Kingdom",
            "Ethiopia", "USA: California", "Mexico: Calnali, Hidalgo"
        ])
        result = engine.parse(s)
        assert result["geo_iso3166"][0] == "BR"
        assert result["geo_region"][1] == "Lima"
        assert result["geo_iso3166"][2] == "GB"
        assert result["geo_iso3166"][3] == "ET"
        assert result["geo_region"][4] == "California"
        assert result["geo_region"][5] == "Calnali"
        assert result["geo_locality"][5] == "Hidalgo"

    def test_null_strings_in_real_data(self, engine):
        s = pd.Series(["not collected", "not applicable", "missing", "USA: California"])
        result = engine.parse(s)
        assert pd.isna(result["geo_country"][0])
        assert pd.isna(result["geo_country"][1])
        assert pd.isna(result["geo_country"][2])
        assert result["geo_country"][3] == "USA"
