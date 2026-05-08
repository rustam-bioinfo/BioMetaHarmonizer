"""
Integration smoke tests for the full harmonization pipeline.

All tests use synthetic in-memory DataFrames that simulate the output of
ingestion -- no live NCBI calls are made.
"""

import numpy as np
import pandas as pd
import pytest

from biometaharmonizer.date_engine import DateEngine
from biometaharmonizer.geo_engine import GeoEngine
from biometaharmonizer.key_mapper import KeyMapper
from biometaharmonizer.one_health import OneHealthClassifier
from biometaharmonizer.output import write


@pytest.fixture(scope="module")
def pipeline_components():
    return {
        "de": DateEngine(),
        "ge": GeoEngine(),
        "km": KeyMapper(),
        "clf": OneHealthClassifier(),
    }


@pytest.fixture
def synthetic_ingested_df():
    """Minimal DataFrame that mimics a post-ingestion result."""
    from biometaharmonizer.ingestion import BIOSAMPLE_SCHEMA
    records = [
        {
            "biosample_accession": "SAMN001",
            "organism_name":       "Escherichia coli",
            "collection_date":     "2021-03-15",
            "geo_loc_name":        "USA: California, Los Angeles",
            "isolation_source":    "blood",
            "host":                "Homo sapiens",
        },
        {
            "biosample_accession": "SAMN002",
            "organism_name":       "Salmonella enterica",
            "collection_date":     "2019-2021",
            "geo_loc_name":        "Germany",
            "isolation_source":    "soil",
            "host":                np.nan,
        },
        {
            "biosample_accession": "SAMN003",
            "organism_name":       "Bacillus cereus",
            "collection_date":     "missing",
            "geo_loc_name":        "not provided",
            "isolation_source":    np.nan,
            "host":                np.nan,
        },
    ]
    df = pd.DataFrame(records)
    for col in BIOSAMPLE_SCHEMA:
        if col not in df.columns:
            df[col] = np.nan
    return df[list(BIOSAMPLE_SCHEMA)]


class TestFullPipeline:
    def test_pipeline_produces_expected_rows(self, pipeline_components, synthetic_ingested_df):
        df = synthetic_ingested_df.copy()
        de = pipeline_components["de"]
        ge = pipeline_components["ge"]
        clf = pipeline_components["clf"]

        date_df = de.parse_with_range(df["collection_date"])
        df["collection_date"] = date_df["collection_date"]
        df["collection_date_range"] = date_df["collection_date_range"]

        geo_df = ge.parse(df["geo_loc_name"])
        for col in geo_df.columns:
            df[col] = geo_df[col]

        oh_df = clf.classify_multi_field(
            isolation_source=df["isolation_source"],
            host=df["host"],
        )
        for col in oh_df.columns:
            df[col] = oh_df[col]

        assert len(df) == 3

    def test_point_date_preserved(self, pipeline_components, synthetic_ingested_df):
        df = synthetic_ingested_df.copy()
        date_df = pipeline_components["de"].parse_with_range(df["collection_date"])
        assert date_df.iloc[0]["collection_date"] == "2021-03-15"

    def test_range_date_set_to_nan(self, pipeline_components, synthetic_ingested_df):
        df = synthetic_ingested_df.copy()
        date_df = pipeline_components["de"].parse_with_range(df["collection_date"])
        assert pd.isna(date_df.iloc[1]["collection_date"])
        assert date_df.iloc[1]["collection_date_range"] == "2019-2021"

    def test_null_date_is_nan(self, pipeline_components, synthetic_ingested_df):
        df = synthetic_ingested_df.copy()
        date_df = pipeline_components["de"].parse_with_range(df["collection_date"])
        assert pd.isna(date_df.iloc[2]["collection_date"])

    def test_geo_country_resolved(self, pipeline_components, synthetic_ingested_df):
        df = synthetic_ingested_df.copy()
        geo_df = pipeline_components["ge"].parse(df["geo_loc_name"])
        assert geo_df.iloc[0]["geo_country"] == "USA"
        assert geo_df.iloc[0]["geo_iso3166"] == "US"
        assert geo_df.iloc[1]["geo_country"] == "Germany"
        assert pd.isna(geo_df.iloc[2]["geo_country"])

    def test_human_record_classified(self, pipeline_components, synthetic_ingested_df):
        df = synthetic_ingested_df.copy()
        oh = pipeline_components["clf"].classify_multi_field(
            isolation_source=df["isolation_source"],
            host=df["host"],
        )
        assert oh.iloc[0]["one_health_category"] == "Human"

    def test_env_record_classified(self, pipeline_components, synthetic_ingested_df):
        df = synthetic_ingested_df.copy()
        oh = pipeline_components["clf"].classify_multi_field(
            isolation_source=df["isolation_source"],
            host=df["host"],
        )
        assert oh.iloc[1]["one_health_category"] == "Environmental"

    def test_unclassified_record_stays_unclassified(self, pipeline_components, synthetic_ingested_df):
        df = synthetic_ingested_df.copy()
        oh = pipeline_components["clf"].classify_multi_field(
            isolation_source=df["isolation_source"],
            host=df["host"],
        )
        assert oh.iloc[2]["one_health_category"] == "Unclassified"

    def test_output_csv_written_correctly(self, tmp_path, pipeline_components, synthetic_ingested_df):
        df = synthetic_ingested_df.copy()
        out = tmp_path / "pipeline_output.csv"
        written = write(df, out, fmt="csv")
        loaded = pd.read_csv(written)
        assert len(loaded) == 3
        assert "biosample_accession" in loaded.columns


class TestCLIArgParser:
    def test_build_parser_run_command_exists(self):
        from biometaharmonizer.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            "run",
            "--input", "ids.txt",
            "--email", "test@example.com",
            "--output", "out.csv",
        ])
        assert args.command == "run"
        assert args.input == "ids.txt"
        assert args.email == "test@example.com"
        assert args.output == "out.csv"

    def test_invalid_format_raises(self):
        from biometaharmonizer.cli import _build_parser
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([
                "run",
                "--input", "ids.txt",
                "--email", "test@example.com",
                "--output", "out.csv",
                "--format", "xml",
            ])

    def test_format_case_insensitive(self):
        from biometaharmonizer.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            "run",
            "--input", "ids.txt",
            "--email", "test@example.com",
            "--output", "out.csv",
            "--format", "CSV",
        ])
        assert args.format == ["csv"]

    def test_infer_format_from_extension(self):
        from pathlib import Path
        from biometaharmonizer.cli import _infer_format
        assert _infer_format(Path("out.csv"))     == "csv"
        assert _infer_format(Path("out.tsv"))     == "tsv"
        assert _infer_format(Path("out.xlsx"))    == "excel"
        assert _infer_format(Path("out.parquet")) == "parquet"
        assert _infer_format(Path("out.jsonl"))   == "jsonl"
        assert _infer_format(Path("out.unknown")) == "csv"
