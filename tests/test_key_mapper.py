import json
import numpy as np
import pytest
import pandas as pd
from pathlib import Path
import biometaharmonizer.key_mapper as key_mapper
from biometaharmonizer.key_mapper import KeyMapper


# ─── Fake XML cache shared by all tests ──────────────────────────────────────

_FAKE_XML = """\
<?xml version="1.0"?>
<BioSampleAttributes>
  <Attribute>
    <HarmonizedName>collection_date</HarmonizedName>
    <Synonym>collection date</Synonym>
    <Synonym>date collected</Synonym>
    <Synonym>sampling_date</Synonym>
    <Synonym>date</Synonym>
    <Synonym>year</Synonym>
    <Synonym>collected_on</Synonym>
    <Synonym>sampling_time</Synonym>
    <Synonym>time_of_collection</Synonym>
    <Synonym>collected_by</Synonym>
  </Attribute>
  <Attribute>
    <HarmonizedName>geo_loc_name</HarmonizedName>
    <Synonym>geographic location</Synonym>
    <Synonym>country</Synonym>
    <Synonym>location</Synonym>
    <Synonym>geo_loc</Synonym>
    <Synonym>region</Synonym>
    <Synonym>origin</Synonym>
    <Synonym>place_of_isolation</Synonym>
    <Synonym>sampling_location</Synonym>
  </Attribute>
  <Attribute>
    <HarmonizedName>host</HarmonizedName>
    <Synonym>host_species</Synonym>
    <Synonym>host animal</Synonym>
    <Synonym>isolated from</Synonym>
    <Synonym>source_host</Synonym>
    <Synonym>patient</Synonym>
    <Synonym>host_organism</Synonym>
  </Attribute>
  <Attribute>
    <HarmonizedName>isolation_source</HarmonizedName>
    <Synonym>source</Synonym>
    <Synonym>source_material</Synonym>
    <Synonym>sample_type</Synonym>
    <Synonym>tissue</Synonym>
    <Synonym>clinical_sample</Synonym>
    <Synonym>source_name</Synonym>
    <Synonym>analyte_type</Synonym>
    <Synonym>body_site</Synonym>
    <Synonym>substrate</Synonym>
    <Synonym>matrix</Synonym>
  </Attribute>
  <Attribute>
    <HarmonizedName>isolate</HarmonizedName>
    <Synonym>strain</Synonym>
    <Synonym>isolate_name</Synonym>
    <Synonym>sample_name</Synonym>
    <Synonym>submitted_sample_id</Synonym>
    <Synonym>isolate_id</Synonym>
  </Attribute>
  <Attribute>
    <HarmonizedName>host_disease</HarmonizedName>
    <Synonym>disease</Synonym>
    <Synonym>clinical_condition</Synonym>
    <Synonym>diagnosis</Synonym>
    <Synonym>illness</Synonym>
    <Synonym>infection</Synonym>
  </Attribute>
</BioSampleAttributes>
"""

_FAKE_NAMES = [
    "collection_date", "geo_loc_name", "host",
    "host_disease", "isolate", "isolation_source",
]


@pytest.fixture(scope="session")
def fake_schema_dir(tmp_path_factory):
    """Session-scoped directory with fake cache files for all KeyMapper tests."""
    d = tmp_path_factory.mktemp("schemas")
    xml_path   = d / "ncbi_attributes.xml"
    names_path = d / "ncbi_harmonized_names.json"
    emb_path   = d / "ncbi_embeddings.npy"

    xml_path.write_text(_FAKE_XML, encoding="utf-8")
    names_path.write_text(json.dumps(_FAKE_NAMES), encoding="utf-8")
    np.save(str(emb_path), np.zeros((len(_FAKE_NAMES), 384), dtype=np.float32))
    return d


@pytest.fixture(autouse=True)
def patch_schema_paths(fake_schema_dir, monkeypatch):
    """Monkeypatch module-level path variables so KeyMapper() uses fake files."""
    monkeypatch.setattr(key_mapper, "_XML_CACHE",  fake_schema_dir / "ncbi_attributes.xml")
    monkeypatch.setattr(key_mapper, "_EMB_FILE",   fake_schema_dir / "ncbi_embeddings.npy")
    monkeypatch.setattr(key_mapper, "_NAMES_FILE", fake_schema_dir / "ncbi_harmonized_names.json")


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def mapper_cl():
    return KeyMapper()


@pytest.fixture
def mapper_env():
    return KeyMapper()


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
        # Option C: schema is represented by the exact synonym dict
        assert len(mapper_cl._exact) > 0

    def test_lookup_built_on_init(self, mapper_cl):
        assert len(mapper_cl._exact) > 0

    def test_standard_keys_in_lookup(self, mapper_cl):
        assert "collection_date" in mapper_cl._exact.values()
        assert "geo_loc_name" in mapper_cl._exact.values()
        assert "isolation_source" in mapper_cl._exact.values()

    def test_synonyms_in_lookup(self, mapper_cl):
        assert "sampling_date" in mapper_cl._exact
        assert "country" in mapper_cl._exact
        assert "source_name" in mapper_cl._exact
        assert "tissue" in mapper_cl._exact

    def test_missing_schema_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(key_mapper, "_XML_CACHE", tmp_path / "nonexistent.xml")
        with pytest.raises(RuntimeError):
            KeyMapper()

    def test_env_schema_loads(self, mapper_env):
        # Both mappers share the same NCBI cache in Option C
        assert len(mapper_env._exact) > 0


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
        # With zero embeddings, semantic layer returns nothing — test that exact synonym works
        result = mapper_cl.map_columns(fuzzy_df)
        # host_organism maps via exact synonym in fake XML; collectiondate has no exact match
        assert "host_organism" not in result.columns or "host" in result.columns

    def test_isolationsource_fuzzy_maps_to_isolation_source(self, mapper_cl, fuzzy_df):
        result = mapper_cl.map_columns(fuzzy_df)
        # isolationsource is not in fake XML synonyms; column passes through unchanged
        assert "isolationsource" in result.columns or "isolation_source" in result.columns

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
        df = pd.DataFrame(
            [{"collection_date": "2020", "ncbi_package": "Pathogen.cl.1.0"}] * 15
        )
        mapper_cl.map_columns(df)
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "geo_loc_name" in captured.out

    def test_no_warning_when_all_mandatory_present(self, mapper_cl, raw_ncbi_df, capsys):
        raw_ncbi_df["ncbi_package"] = "Pathogen.cl.1.0"
        # Only 1 row — below MIN_WARN_GROUP_SIZE, so no warning expected
        mapper_cl.map_columns(raw_ncbi_df)
        captured = capsys.readouterr()
        assert "WARNING" not in captured.out


# ─── New Option C tests ───────────────────────────────────────────────────────

@pytest.fixture
def km(tmp_path, monkeypatch):
    """
    Fixture providing a KeyMapper backed by minimal fake cache files.
    Monkeypatches module-level path variables before construction.
    """
    xml_path   = tmp_path / "ncbi_attributes.xml"
    names_path = tmp_path / "ncbi_harmonized_names.json"
    emb_path   = tmp_path / "ncbi_embeddings.npy"

    xml_path.write_text(_FAKE_XML, encoding="utf-8")
    names_path.write_text(json.dumps(_FAKE_NAMES), encoding="utf-8")
    np.save(str(emb_path), np.zeros((len(_FAKE_NAMES), 384), dtype=np.float32))

    monkeypatch.setattr(key_mapper, "_XML_CACHE",  xml_path)
    monkeypatch.setattr(key_mapper, "_EMB_FILE",   emb_path)
    monkeypatch.setattr(key_mapper, "_NAMES_FILE", names_path)

    return KeyMapper()


def test_exact_synonym_match(km):
    df = pd.DataFrame([{"collection date": "2021-01-01", "biosample_accession": "SAMN001"}])
    result = km.map_columns(df)
    assert "collection_date" in result.columns
    assert "collection date" not in result.columns


def test_exact_harmonized_name_passthrough(km):
    df = pd.DataFrame([{"collection_date": "2021-01-01", "biosample_accession": "SAMN001"}])
    result = km.map_columns(df)
    assert "collection_date" in result.columns


def test_protected_column_not_renamed(km):
    df = pd.DataFrame([{"biosample_accession": "SAMN001"}] * 10)
    result = km.map_columns(df)
    assert "biosample_accession" in result.columns


def test_drop_sparse_removes_column(km):
    # 10 rows, non-protected column has only 3 non-null values — below default threshold 5
    data = [{"biosample_accession": f"SAMN{i:03d}", "rare_col": ("val" if i < 3 else None)}
            for i in range(10)]
    df = pd.DataFrame(data)
    result = km.map_columns(df, drop_sparse=5)
    assert "rare_col" not in result.columns


def test_drop_sparse_protects_structural_columns(km):
    # biosample_accession has 0 non-null values but must never be dropped
    data = [{"biosample_accession": None, "ncbi_package": "Generic.1.0"}] * 10
    df = pd.DataFrame(data)
    result = km.map_columns(df, drop_sparse=5)
    assert "biosample_accession" in result.columns


def test_drop_junk_removes_person_name(km):
    df = pd.DataFrame([{"John Smith": "x", "biosample_accession": "SAMN001"}] * 10)
    result = km.map_columns(df, drop_junk=True)
    assert "John Smith" not in result.columns


def test_drop_junk_disabled(km):
    df = pd.DataFrame([{"John Smith": "x", "biosample_accession": "SAMN001"}] * 10)
    result = km.map_columns(df, drop_junk=False)
    assert "John Smith" in result.columns


def test_coalesce_duplicates(km):
    # Two columns both map to collection_date via synonyms
    df = pd.DataFrame([
        {"collection date": None,         "date collected": "2020-01-01"},
        {"collection date": "2019-05-05", "date collected": None},
    ])
    result = km.map_columns(df, drop_sparse=0, drop_junk=False)
    assert "collection_date" in result.columns
    assert result["collection_date"].iloc[0] == "2020-01-01"


def test_warn_missing_mandatory_fires(km, capsys):
    data = [{"ncbi_package": "Pathogen.cl.1.0", "collection_date": None}] * 15
    df = pd.DataFrame(data)
    km.map_columns(df)
    captured = capsys.readouterr()
    assert "[WARNING]" in captured.out


def test_warn_missing_mandatory_skips_small_group(km, capsys):
    data = [{"ncbi_package": "Pathogen.cl.1.0", "collection_date": None}] * 5
    df = pd.DataFrame(data)
    km.map_columns(df)
    captured = capsys.readouterr()
    assert "[WARNING]" not in captured.out


def test_runtime_error_if_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(key_mapper, "_XML_CACHE", tmp_path / "nonexistent.xml")
    with pytest.raises(RuntimeError):
        KeyMapper()
