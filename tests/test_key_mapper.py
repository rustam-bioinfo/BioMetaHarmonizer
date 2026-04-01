import pytest
import json
import pandas as pd
from pathlib import Path
from biometaharmonizer.key_mapper import KeyMapper


SCHEMA_CL = Path("schemas/pathogen_cl_1.0.json")
SCHEMA_ENV = Path("schemas/pathogen_env_1.0.json")


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def mapper_cl():
    return KeyMapper(SCHEMA_CL)


@pytest.fixture
def mapper_env():
    return KeyMapper(SCHEMA_ENV)


@pytest.fixture
def raw_ncbi_df():
    """Simulates real column names returned by NCBI BioSample for B. cereus."""
    return pd.DataFrame([{
        "analyte_type": "DNA",
        "biosample_accession": "SAMN12345678",
        "biosample_id": "12345678",
        "collected_by": "Lab X",
        "collection_date": "2021-05-12",
        "diagnosis": "bacteremia",
        "geo_loc_name": "Russia: Moscow",
        "host": "Homo sapiens",
        "host_disease": "sepsis",
        "isolate": "BCR-001",
        "isolation_source": "blood",
        "sex": "male",
        "source_name": "blood culture",
        "strain": "BCR-001",
        "submitted_sample_id": "LAB-001",
        "tissue": "blood",
    }])


@pytest.fixture
def messy_synonym_df():
    """Simulates messy free-text column names submitted by different labs."""
    return pd.DataFrame([{
        "sampling_date": "2019-07-01",
        "country": "Germany: Berlin",
        "host_species": "Homo sapiens",
        "source": "wound swab",
        "disease": "wound infection",
        "strain": "STR-42",
    }])


@pytest.fixture
def fuzzy_df():
    """Simulates slightly misspelled or variant column names for fuzzy matching."""
    return pd.DataFrame([{
        "collectiondate": "2020-01-15",
        "geolocation": "France: Paris",
        "host_organism": "Homo sapiens",
        "isolationsource": "urine",
    }])


# ─── Initialization ───────────────────────────────────────────────────────────

class TestKeyMapperInit:

    def test_loads_valid_schema(self, mapper_cl):
        assert mapper_cl.schema["schema_name"] == "Pathogen.cl.1.0"

    def test_lookup_built_on_init(self, mapper_cl):
        assert len(mapper_cl.lookup) > 0

    def test_standard_keys_in_lookup(self, mapper_cl):
        assert "collection_date" in mapper_cl.lookup.values()
        assert "geo_loc_name" in mapper_cl.lookup.values()
        assert "isolation_source" in mapper_cl.lookup.values()

    def test_synonyms_in_lookup(self, mapper_cl):
        assert "sampling_date" in mapper_cl.lookup
        assert "country" in mapper_cl.lookup
        assert "source_name" in mapper_cl.lookup
        assert "tissue" in mapper_cl.lookup

    def test_missing_schema_raises(self):
        with pytest.raises(FileNotFoundError):
            KeyMapper("/nonexistent/schema.json")

    def test_env_schema_loads(self, mapper_env):
        assert mapper_env.schema["schema_name"] == "Pathogen.env.1.0"


# ─── Exact matching ──────────────────────────────────────────────────────────

class TestExactMatching:

    def test_standard_keys_pass_through(self, mapper_cl, raw_ncbi_df):
        result = mapper_cl.map_columns(raw_ncbi_df)
        assert "collection_date" in result.columns
        assert "geo_loc_name" in result.columns
        assert "isolation_source" in result.columns

    def test_synonym_sampling_date_maps_to_collection_date(self, mapper_cl, messy_synonym_df):
        result = mapper_cl.map_columns(messy_synonym_df)
        assert "collection_date" in result.columns
        assert "sampling_date" not in result.columns

    def test_synonym_country_maps_to_geo_loc_name(self, mapper_cl, messy_synonym_df):
        result = mapper_cl.map_columns(messy_synonym_df)
        assert "geo_loc_name" in result.columns
        assert "country" not in result.columns

    def test_synonym_source_maps_to_isolation_source(self, mapper_cl, messy_synonym_df):
        result = mapper_cl.map_columns(messy_synonym_df)
        assert "isolation_source" in result.columns

    def test_synonym_strain_maps_to_isolate(self, mapper_cl, messy_synonym_df):
        result = mapper_cl.map_columns(messy_synonym_df)
        assert "isolate" in result.columns
        assert "strain" not in result.columns

    def test_synonym_disease_maps_to_host_disease(self, mapper_cl, messy_synonym_df):
        result = mapper_cl.map_columns(messy_synonym_df)
        assert "host_disease" in result.columns

    def test_data_values_preserved_after_rename(self, mapper_cl, messy_synonym_df):
        result = mapper_cl.map_columns(messy_synonym_df)
        assert result["collection_date"].iloc[0] == "2019-07-01"
        assert result["geo_loc_name"].iloc[0] == "Germany: Berlin"

    def test_unrecognized_columns_preserved(self, mapper_cl):
        df = pd.DataFrame([{"totally_unknown_col": "value", "collection_date": "2020"}])
        result = mapper_cl.map_columns(df)
        assert "totally_unknown_col" in result.columns


# ─── Fuzzy matching ──────────────────────────────────────────────────────────

class TestFuzzyMatching:

    def test_collectiondate_fuzzy_maps_to_collection_date(self, mapper_cl, fuzzy_df):
        result = mapper_cl.map_columns(fuzzy_df)
        assert "collection_date" in result.columns

    def test_isolationsource_fuzzy_maps_to_isolation_source(self, mapper_cl, fuzzy_df):
        result = mapper_cl.map_columns(fuzzy_df)
        assert "isolation_source" in result.columns

    def test_fuzzy_does_not_merge_unrelated_columns(self, mapper_cl):
        df = pd.DataFrame([{"study_name": "cohort_A", "collection_date": "2020"}])
        result = mapper_cl.map_columns(df)
        assert "study_name" in result.columns
        assert "collection_date" in result.columns


# ─── Parser routing ─────────────────────────────────────────────────────────

class TestParserRouting:

    def test_collection_date_routes_to_date_engine(self, mapper_cl):
        routing = mapper_cl.get_parser_routing()
        assert routing["collection_date"] == "date_engine"

    def test_geo_loc_name_routes_to_geo_engine(self, mapper_cl):
        routing = mapper_cl.get_parser_routing()
        assert routing["geo_loc_name"] == "geo_engine"

    def test_isolation_source_routes_to_one_health_engine(self, mapper_cl):
        routing = mapper_cl.get_parser_routing()
        assert routing["isolation_source"] == "one_health_engine"

    def test_host_routes_to_one_health_engine(self, mapper_cl):
        routing = mapper_cl.get_parser_routing()
        assert routing["host"] == "one_health_engine"

    def test_isolate_routes_to_string_cleaner(self, mapper_cl):
        routing = mapper_cl.get_parser_routing()
        assert routing["isolate"] == "string_cleaner"


# ─── Mandatory field warnings ───────────────────────────────────────────────

class TestMandatoryWarnings:

    def test_missing_mandatory_field_prints_warning(self, mapper_cl, capsys):
        df = pd.DataFrame([{"collection_date": "2020"}])
        mapper_cl.map_columns(df)
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "geo_loc_name" in captured.out

    def test_no_warning_when_all_mandatory_present(self, mapper_cl, raw_ncbi_df, capsys):
        mapper_cl.map_columns(raw_ncbi_df)
        captured = capsys.readouterr()
        assert "WARNING" not in captured.out
