"""
End-to-end integration tests for the BioMetaHarmonizer pipeline.
No real NCBI network calls — all inputs are synthetic DataFrames.
"""

import json
import numpy as np
import pytest
import pandas as pd
import biometaharmonizer.key_mapper as key_mapper
from biometaharmonizer.key_mapper import KeyMapper
from biometaharmonizer.date_engine import DateEngine
from biometaharmonizer.geo_engine import GeoEngine
from biometaharmonizer.one_health import OneHealthClassifier
from biometaharmonizer.output import write


# ─── Fake XML reused from test_key_mapper ────────────────────────────────────

_FAKE_XML = """\
<?xml version="1.0"?>
<BioSampleAttributes>
  <Attribute>
    <HarmonizedName>collection_date</HarmonizedName>
    <Synonym>collection date</Synonym>
    <Synonym>date collected</Synonym>
    <Synonym>sampling_date</Synonym>
  </Attribute>
  <Attribute>
    <HarmonizedName>geo_loc_name</HarmonizedName>
    <Synonym>geographic location</Synonym>
    <Synonym>country</Synonym>
  </Attribute>
  <Attribute>
    <HarmonizedName>host</HarmonizedName>
    <Synonym>host_organism</Synonym>
  </Attribute>
  <Attribute>
    <HarmonizedName>isolation_source</HarmonizedName>
    <Synonym>source</Synonym>
  </Attribute>
</BioSampleAttributes>
"""

_FAKE_NAMES = ["collection_date", "geo_loc_name", "host", "isolation_source"]


@pytest.fixture(scope="module")
def fake_schema_dir_pipeline(tmp_path_factory):
    d = tmp_path_factory.mktemp("pipeline_schemas")
    (d / "ncbi_attributes.xml").write_text(_FAKE_XML, encoding="utf-8")
    (d / "ncbi_harmonized_names.json").write_text(json.dumps(_FAKE_NAMES), encoding="utf-8")
    np.save(str(d / "ncbi_embeddings.npy"), np.zeros((len(_FAKE_NAMES), 384), dtype=np.float32))
    return d


@pytest.fixture(autouse=True)
def patch_pipeline_paths(fake_schema_dir_pipeline, monkeypatch):
    monkeypatch.setattr(key_mapper, "_XML_CACHE",  fake_schema_dir_pipeline / "ncbi_attributes.xml")
    monkeypatch.setattr(key_mapper, "_EMB_FILE",   fake_schema_dir_pipeline / "ncbi_embeddings.npy")
    monkeypatch.setattr(key_mapper, "_NAMES_FILE", fake_schema_dir_pipeline / "ncbi_harmonized_names.json")


@pytest.fixture
def km():
    return KeyMapper()


@pytest.fixture
def mock_df():
    """Simulates a 10-row post-ingestion DataFrame with raw column names."""
    return pd.DataFrame({
        "biosample_accession":  [f"SAMN{i:08d}" for i in range(1, 11)],
        "biosample_id":         [str(i) for i in range(1, 11)],
        "sra_accession":        [f"SRR{i:07d}" for i in range(1, 11)],
        "bioproject_accession": [f"PRJNA{i:06d}" for i in range(1, 11)],
        "taxonomy_id":          ["1396"] * 10,
        "taxonomy_name":        ["Bacillus cereus"] * 10,
        "organism_name":        ["Bacillus cereus"] * 10,
        "ncbi_package":         ["Pathogen.cl.1.0"] * 10,
        "submission_date":      ["2020-01-15"] * 10,
        "last_update":          ["2021-06-30"] * 10,
        "collection date": [
            "2020-01-15", "2019-06-30", "2018-03-10", "2021-11-22",
            "2017-07-04", "2020-09-01", "2019-12-31", "2018-08-15",
            "2021-03-25", "2020-05-20",
        ],
        "geographic location": [
            "Russia: Moscow",   "USA: California",  "Germany: Berlin",
            "China: Beijing",   "India: Maharashtra", "Brazil: Sao Paulo",
            "UK: England",      "France: Paris",    "Japan: Tokyo",
            "Australia: Queensland",
        ],
        "host organism": [
            "Homo sapiens", "Bos taurus", "Sus scrofa", "Gallus gallus",
            "Homo sapiens", "Bos taurus", "Homo sapiens", "Sus scrofa",
            "Gallus gallus", "Homo sapiens",
        ],
        "isolation_source": [
            "blood", "milk", "feces", "liver",
            "urine", "soil", "clinical swab", "intestine",
            "feather", "wound",
        ],
    })


# ─── Tests ────────────────────────────────────────────────────────────────────

def test_pipeline_keymapper_renames(km, mock_df):
    result = km.map_columns(mock_df, drop_sparse=0)
    assert "collection_date" in result.columns
    assert "collection date" not in result.columns
    assert "geo_loc_name" in result.columns
    assert "geographic location" not in result.columns


def test_pipeline_date_engine(mock_df):
    # DateEngine.parse() operates on a Series and returns a parsed Series
    mock_df = mock_df.rename(columns={"collection date": "collection_date"})
    engine = DateEngine()
    parsed = engine.parse(mock_df["collection_date"])
    assert parsed is not None
    assert len(parsed) == len(mock_df)
    # At least some values should be valid ISO dates
    non_null = parsed.dropna()
    assert len(non_null) > 0


def test_pipeline_geo_engine(mock_df):
    # GeoEngine.parse() operates on a Series and returns a DataFrame
    # with columns: Country, Region, Locality, ISO3166
    mock_df = mock_df.rename(columns={"geographic location": "geo_loc_name"})
    engine = GeoEngine()
    geo_df = engine.parse(mock_df["geo_loc_name"])
    assert isinstance(geo_df, pd.DataFrame)
    assert "Country" in geo_df.columns
    non_null_countries = geo_df["Country"].dropna()
    assert len(non_null_countries) > 0


def test_pipeline_one_health(mock_df):
    # OneHealthClassifier.classify() operates on a Series and returns a Series
    engine = OneHealthClassifier()
    result = engine.classify(mock_df["isolation_source"])
    assert result is not None
    assert len(result) == len(mock_df)
    non_null = result.dropna()
    assert len(non_null) > 0


def test_pipeline_output(km, mock_df, tmp_path):
    df = km.map_columns(mock_df, drop_sparse=0)
    out = write(df, tmp_path / "out.csv")
    assert out.exists()
    result = pd.read_csv(out)
    assert len(result) == len(mock_df)
