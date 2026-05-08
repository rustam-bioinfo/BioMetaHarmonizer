"""
Integration smoke tests for the full harmonization pipeline
and unit tests for all CLI helper functions.

All tests use synthetic in-memory DataFrames that simulate the output of
ingestion -- no live NCBI calls are made.
"""

import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from biometaharmonizer.cli import (
    _build_parser,
    _get_version,
    _infer_format,
    _looks_like_filepath,
    _lower_format,
    _resolve_output_targets,
    _run,
)
from biometaharmonizer.date_engine import DateEngine
from biometaharmonizer.geo_engine import GeoEngine
from biometaharmonizer.key_mapper import KeyMapper
from biometaharmonizer.one_health import OneHealthClassifier
from biometaharmonizer.output import write, write_summary


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers: _lower_format
# ---------------------------------------------------------------------------

class TestLowerFormat:
    def test_valid_lowercase_unchanged(self):
        assert _lower_format("csv") == "csv"

    def test_valid_mixed_case_lowercased(self):
        assert _lower_format("CSV") == "csv"
        assert _lower_format("Parquet") == "parquet"
        assert _lower_format("JSONL") == "jsonl"

    def test_invalid_raises_argument_type_error(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _lower_format("xml")

    def test_all_valid_formats_accepted(self):
        for fmt in ("csv", "tsv", "excel", "parquet", "jsonl"):
            assert _lower_format(fmt) == fmt


# ---------------------------------------------------------------------------
# Helpers: _infer_format
# ---------------------------------------------------------------------------

class TestInferFormat:
    def test_csv_extension(self):
        assert _infer_format(Path("out.csv")) == "csv"

    def test_tsv_extension(self):
        assert _infer_format(Path("out.tsv")) == "tsv"

    def test_txt_extension_returns_tsv(self):
        assert _infer_format(Path("out.txt")) == "tsv"

    def test_xlsx_extension(self):
        assert _infer_format(Path("out.xlsx")) == "excel"

    def test_xls_extension_returns_excel(self):
        assert _infer_format(Path("out.xls")) == "excel"

    def test_parquet_extension(self):
        assert _infer_format(Path("out.parquet")) == "parquet"

    def test_jsonl_extension(self):
        assert _infer_format(Path("out.jsonl")) == "jsonl"

    def test_unknown_extension_returns_csv(self):
        assert _infer_format(Path("out.unknown")) == "csv"

    def test_no_extension_returns_csv(self):
        assert _infer_format(Path("outfile")) == "csv"

    def test_uppercase_extension_normalised(self):
        assert _infer_format(Path("out.CSV")) == "csv"
        assert _infer_format(Path("out.TSV")) == "tsv"
        assert _infer_format(Path("out.PARQUET")) == "parquet"


# ---------------------------------------------------------------------------
# Helpers: _resolve_output_targets
# ---------------------------------------------------------------------------

class TestResolveOutputTargets:
    def test_single_format_path_unchanged(self):
        p = Path("/data/out.csv")
        targets = _resolve_output_targets(p, ["csv"])
        assert targets == [("csv", p)]

    def test_multi_format_csv_extension(self):
        p = Path("/data/out.csv")
        targets = _resolve_output_targets(p, ["csv", "tsv"])
        fmts = {fmt for fmt, _ in targets}
        paths = {str(path) for _, path in targets}
        assert fmts == {"csv", "tsv"}
        assert any(str(path).endswith(".csv") for path in paths)
        assert any(str(path).endswith(".tsv") for path in paths)

    def test_multi_format_excel_gets_xlsx(self):
        p = Path("/data/out.csv")
        targets = _resolve_output_targets(p, ["csv", "excel"])
        excel_path = next(path for fmt, path in targets if fmt == "excel")
        assert str(excel_path).endswith(".xlsx")

    def test_multi_format_stem_preserved(self):
        p = Path("/data/myresults.csv")
        targets = _resolve_output_targets(p, ["csv", "parquet"])
        for _, path in targets:
            assert path.stem == "myresults"

    def test_multi_format_all_formats(self):
        p = Path("/data/out.csv")
        fmts = ["csv", "tsv", "excel", "parquet", "jsonl"]
        targets = _resolve_output_targets(p, fmts)
        assert len(targets) == 5
        result_fmts = [fmt for fmt, _ in targets]
        assert result_fmts == fmts


# ---------------------------------------------------------------------------
# Helpers: _looks_like_filepath
# ---------------------------------------------------------------------------

class TestLooksLikeFilepath:
    def test_plain_accession_no_extension_is_false(self):
        assert _looks_like_filepath("SAMN001234567") is False

    def test_plain_gcf_accession_is_false(self):
        assert _looks_like_filepath("GCF_000005845") is False

    def test_plain_gca_accession_is_false(self):
        assert _looks_like_filepath("GCA_000001405") is False

    def test_filename_with_extension_is_true(self):
        assert _looks_like_filepath("ids.txt") is True

    def test_absolute_path_with_extension_is_true(self):
        assert _looks_like_filepath("/data/ids.txt") is True

    def test_accession_with_extension_is_false(self):
        assert _looks_like_filepath("SAMN001234.csv") is False

    def test_csv_filename_is_true(self):
        assert _looks_like_filepath("output.csv") is True


# ---------------------------------------------------------------------------
# Helpers: _get_version
# ---------------------------------------------------------------------------

class TestGetVersion:
    def test_returns_nonempty_string(self):
        result = _get_version()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_unknown_when_import_fails(self):
        with patch("biometaharmonizer.cli._get_version") as mock_gv:
            mock_gv.return_value = "unknown"
            assert mock_gv() == "unknown"


# ---------------------------------------------------------------------------
# _build_parser
# ---------------------------------------------------------------------------

class TestBuildParser:
    def test_run_required_args(self):
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

    def test_missing_input_exits(self):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["run", "--email", "a@b.com", "--output", "out.csv"])

    def test_missing_email_exits(self):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["run", "--input", "ids.txt", "--output", "out.csv"])

    def test_missing_output_exits(self):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["run", "--input", "ids.txt", "--email", "a@b.com"])

    def test_no_subcommand_exits(self):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_api_key_parsed(self):
        parser = _build_parser()
        args = parser.parse_args([
            "run", "--input", "ids.txt",
            "--email", "a@b.com",
            "--output", "out.csv",
            "--api-key", "MYKEY123",
        ])
        assert args.api_key == "MYKEY123"

    def test_cache_dir_parsed(self):
        parser = _build_parser()
        args = parser.parse_args([
            "run", "--input", "ids.txt",
            "--email", "a@b.com",
            "--output", "out.csv",
            "--cache-dir", "/tmp/cache",
        ])
        assert args.cache_dir == "/tmp/cache"

    def test_summary_parsed(self):
        parser = _build_parser()
        args = parser.parse_args([
            "run", "--input", "ids.txt",
            "--email", "a@b.com",
            "--output", "out.csv",
            "--summary", "summary.csv",
        ])
        assert args.summary == "summary.csv"

    def test_verbose_default_false(self):
        parser = _build_parser()
        args = parser.parse_args([
            "run", "--input", "ids.txt",
            "--email", "a@b.com",
            "--output", "out.csv",
        ])
        assert args.verbose is False

    def test_verbose_flag(self):
        parser = _build_parser()
        args = parser.parse_args([
            "run", "--input", "ids.txt",
            "--email", "a@b.com",
            "--output", "out.csv",
            "--verbose",
        ])
        assert args.verbose is True

    def test_fetch_batch_size_default(self):
        parser = _build_parser()
        args = parser.parse_args([
            "run", "--input", "ids.txt",
            "--email", "a@b.com",
            "--output", "out.csv",
        ])
        assert args.fetch_batch_size == 200

    def test_fetch_batch_size_custom(self):
        parser = _build_parser()
        args = parser.parse_args([
            "run", "--input", "ids.txt",
            "--email", "a@b.com",
            "--output", "out.csv",
            "--fetch-batch-size", "500",
        ])
        assert args.fetch_batch_size == 500

    def test_esearch_batch_size_default(self):
        parser = _build_parser()
        args = parser.parse_args([
            "run", "--input", "ids.txt",
            "--email", "a@b.com",
            "--output", "out.csv",
        ])
        assert args.esearch_batch_size == 200

    def test_refresh_cache_flag(self):
        parser = _build_parser()
        args = parser.parse_args([
            "run", "--input", "ids.txt",
            "--email", "a@b.com",
            "--output", "out.csv",
            "--refresh-cache",
        ])
        assert args.refresh_cache is True

    def test_multi_format_produces_list(self):
        parser = _build_parser()
        args = parser.parse_args([
            "run", "--input", "ids.txt",
            "--email", "a@b.com",
            "--output", "out.csv",
            "--format", "csv", "tsv", "excel",
        ])
        assert args.format == ["csv", "tsv", "excel"]

    def test_invalid_format_raises(self):
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
        parser = _build_parser()
        args = parser.parse_args([
            "run",
            "--input", "ids.txt",
            "--email", "test@example.com",
            "--output", "out.csv",
            "--format", "CSV",
        ])
        assert args.format == ["csv"]

    def test_version_flag_raises_system_exit(self):
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# _run -- unit tests via mocked ingestion
# ---------------------------------------------------------------------------

def _make_run_args(tmp_path, **kwargs):
    """Build a minimal Namespace that _run accepts."""
    defaults = dict(
        input=str(tmp_path / "ids.txt"),
        email="test@example.com",
        output=str(tmp_path / "out.csv"),
        api_key=None,
        cache_dir=None,
        format=None,
        summary=None,
        verbose=False,
        fetch_batch_size=200,
        esearch_batch_size=200,
        refresh_cache=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


@pytest.fixture
def ids_file(tmp_path):
    p = tmp_path / "ids.txt"
    p.write_text("SAMN001\nSAMN002\n", encoding="utf-8")
    return p


@pytest.fixture
def minimal_df():
    from biometaharmonizer.ingestion import BIOSAMPLE_SCHEMA
    records = [
        {
            "biosample_accession": "SAMN001",
            "organism_name":       "Escherichia coli",
            "collection_date":     "2021-03-15",
            "geo_loc_name":        "Germany",
            "isolation_source":    "blood",
            "host":                "Homo sapiens",
        },
    ]
    df = pd.DataFrame(records)
    for col in BIOSAMPLE_SCHEMA:
        if col not in df.columns:
            df[col] = np.nan
    return df[list(BIOSAMPLE_SCHEMA)]


class TestRunFunction:
    def test_nonexistent_filepath_returns_1(self, tmp_path):
        args = _make_run_args(
            tmp_path,
            input=str(tmp_path / "no_such_file.txt"),
        )
        code = _run(args)
        assert code == 1

    def test_empty_accession_list_returns_1(self, tmp_path):
        args = _make_run_args(tmp_path, input=",,,")
        code = _run(args)
        assert code == 1

    def test_comma_separated_accessions_calls_ingest(self, tmp_path, minimal_df):
        args = _make_run_args(tmp_path, input="SAMN001,SAMN002")
        with patch("biometaharmonizer.ingestion.set_email"), \
             patch("biometaharmonizer.ingestion.ingest", return_value=minimal_df.copy()) as mock_ingest:
            _run(args)
        call_source = mock_ingest.call_args[0][0]
        assert isinstance(call_source, list)
        assert "SAMN001" in call_source

    def test_ingest_exception_returns_2(self, tmp_path, ids_file):
        args = _make_run_args(tmp_path, input=str(ids_file))
        with patch("biometaharmonizer.ingestion.set_email"), \
             patch("biometaharmonizer.ingestion.ingest", side_effect=RuntimeError("network error")):
            code = _run(args)
        assert code == 2

    def test_ingest_empty_df_returns_2(self, tmp_path, ids_file):
        from biometaharmonizer.ingestion import BIOSAMPLE_SCHEMA
        empty = pd.DataFrame(columns=list(BIOSAMPLE_SCHEMA))
        args = _make_run_args(tmp_path, input=str(ids_file))
        with patch("biometaharmonizer.ingestion.set_email"), \
             patch("biometaharmonizer.ingestion.ingest", return_value=empty):
            code = _run(args)
        assert code == 2

    def test_successful_run_returns_0(self, tmp_path, ids_file, minimal_df, capsys):
        out = tmp_path / "out.csv"
        args = _make_run_args(tmp_path, input=str(ids_file), output=str(out))
        with patch("biometaharmonizer.ingestion.set_email"), \
             patch("biometaharmonizer.ingestion.ingest", return_value=minimal_df.copy()):
            code = _run(args)
        assert code == 0
        captured = capsys.readouterr()
        assert "Done" in captured.out

    def test_output_file_created_on_success(self, tmp_path, ids_file, minimal_df):
        out = tmp_path / "out.csv"
        args = _make_run_args(tmp_path, input=str(ids_file), output=str(out))
        with patch("biometaharmonizer.ingestion.set_email"), \
             patch("biometaharmonizer.ingestion.ingest", return_value=minimal_df.copy()):
            _run(args)
        assert out.exists()

    def test_summary_written_when_arg_given(self, tmp_path, ids_file, minimal_df):
        out = tmp_path / "out.csv"
        summary = tmp_path / "summary.csv"
        args = _make_run_args(
            tmp_path,
            input=str(ids_file),
            output=str(out),
            summary=str(summary),
        )
        with patch("biometaharmonizer.ingestion.set_email"), \
             patch("biometaharmonizer.ingestion.ingest", return_value=minimal_df.copy()):
            code = _run(args)
        assert code == 0
        assert summary.exists()

    def test_multi_format_writes_multiple_files(self, tmp_path, ids_file, minimal_df):
        out = tmp_path / "out.csv"
        args = _make_run_args(
            tmp_path,
            input=str(ids_file),
            output=str(out),
            format=["csv", "tsv"],
        )
        with patch("biometaharmonizer.ingestion.set_email"), \
             patch("biometaharmonizer.ingestion.ingest", return_value=minimal_df.copy()):
            code = _run(args)
        assert code == 0
        assert (tmp_path / "out.csv").exists()
        assert (tmp_path / "out.tsv").exists()

    def test_write_failure_returns_2(self, tmp_path, ids_file, minimal_df):
        out = tmp_path / "out.csv"
        args = _make_run_args(tmp_path, input=str(ids_file), output=str(out))
        with patch("biometaharmonizer.ingestion.set_email"), \
             patch("biometaharmonizer.ingestion.ingest", return_value=minimal_df.copy()), \
             patch("biometaharmonizer.cli.write", side_effect=OSError("disk full")):
            code = _run(args)
        assert code == 2

    def test_format_inferred_from_extension(self, tmp_path, ids_file, minimal_df):
        out = tmp_path / "out.tsv"
        args = _make_run_args(tmp_path, input=str(ids_file), output=str(out))
        with patch("biometaharmonizer.ingestion.set_email"), \
             patch("biometaharmonizer.ingestion.ingest", return_value=minimal_df.copy()):
            code = _run(args)
        assert code == 0
        assert out.exists()
        header = out.read_text(encoding="utf-8").splitlines()[0]
        assert "\t" in header


# ---------------------------------------------------------------------------
# TestFullPipeline
# ---------------------------------------------------------------------------

class TestFullPipeline:
    def _run_pipeline(self, pipeline_components, df):
        """Execute all pipeline steps and return the enriched DataFrame."""
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

        return df

    def test_pipeline_produces_expected_rows(self, pipeline_components, synthetic_ingested_df):
        df = self._run_pipeline(pipeline_components, synthetic_ingested_df.copy())
        assert len(df) == 3

    def test_point_date_preserved(self, pipeline_components, synthetic_ingested_df):
        date_df = pipeline_components["de"].parse_with_range(
            synthetic_ingested_df["collection_date"]
        )
        assert date_df.iloc[0]["collection_date"] == "2021-03-15"

    def test_range_date_set_to_nan(self, pipeline_components, synthetic_ingested_df):
        date_df = pipeline_components["de"].parse_with_range(
            synthetic_ingested_df["collection_date"]
        )
        assert pd.isna(date_df.iloc[1]["collection_date"])
        assert date_df.iloc[1]["collection_date_range"] == "2019-2021"

    def test_null_date_is_nan(self, pipeline_components, synthetic_ingested_df):
        date_df = pipeline_components["de"].parse_with_range(
            synthetic_ingested_df["collection_date"]
        )
        assert pd.isna(date_df.iloc[2]["collection_date"])

    def test_collection_date_range_column_present(self, pipeline_components, synthetic_ingested_df):
        df = self._run_pipeline(pipeline_components, synthetic_ingested_df.copy())
        assert "collection_date_range" in df.columns

    def test_geo_country_resolved(self, pipeline_components, synthetic_ingested_df):
        geo_df = pipeline_components["ge"].parse(
            synthetic_ingested_df["geo_loc_name"]
        )
        assert geo_df.iloc[0]["geo_country"] == "USA"
        assert geo_df.iloc[1]["geo_country"] == "Germany"
        assert pd.isna(geo_df.iloc[2]["geo_country"])

    def test_geo_iso3166_resolved(self, pipeline_components, synthetic_ingested_df):
        geo_df = pipeline_components["ge"].parse(
            synthetic_ingested_df["geo_loc_name"]
        )
        assert geo_df.iloc[0]["geo_iso3166"] == "US"
        assert geo_df.iloc[1]["geo_iso3166"] == "DE"

    def test_human_record_classified(self, pipeline_components, synthetic_ingested_df):
        oh = pipeline_components["clf"].classify_multi_field(
            isolation_source=synthetic_ingested_df["isolation_source"],
            host=synthetic_ingested_df["host"],
        )
        assert oh.iloc[0]["one_health_category"] == "Human"

    def test_env_record_classified(self, pipeline_components, synthetic_ingested_df):
        oh = pipeline_components["clf"].classify_multi_field(
            isolation_source=synthetic_ingested_df["isolation_source"],
            host=synthetic_ingested_df["host"],
        )
        assert oh.iloc[1]["one_health_category"] == "Environmental"

    def test_unclassified_record_stays_unclassified(self, pipeline_components, synthetic_ingested_df):
        oh = pipeline_components["clf"].classify_multi_field(
            isolation_source=synthetic_ingested_df["isolation_source"],
            host=synthetic_ingested_df["host"],
        )
        assert oh.iloc[2]["one_health_category"] == "Unclassified"

    def test_one_health_columns_present(self, pipeline_components, synthetic_ingested_df):
        df = self._run_pipeline(pipeline_components, synthetic_ingested_df.copy())
        for col in ("one_health_category", "one_health_confidence", "one_health_evidence_level"):
            assert col in df.columns, f"missing column: {col}"

    def test_all_categories_are_valid(self, pipeline_components, synthetic_ingested_df):
        from biometaharmonizer.one_health import _VALID_CATEGORIES
        df = self._run_pipeline(pipeline_components, synthetic_ingested_df.copy())
        for val in df["one_health_category"]:
            assert val in _VALID_CATEGORIES, f"invalid category: {val!r}"

    def test_output_csv_written_correctly(self, tmp_path, pipeline_components, synthetic_ingested_df):
        df = synthetic_ingested_df.copy()
        out = tmp_path / "pipeline_output.csv"
        written = write(df, out, fmt="csv")
        loaded = pd.read_csv(written)
        assert len(loaded) == 3
        assert "biosample_accession" in loaded.columns

    def test_output_tsv_round_trip(self, tmp_path, pipeline_components, synthetic_ingested_df):
        df = self._run_pipeline(pipeline_components, synthetic_ingested_df.copy())
        out = tmp_path / "pipeline_output.tsv"
        write(df, out, fmt="tsv")
        loaded = pd.read_csv(out, sep="\t")
        assert len(loaded) == 3
        assert "biosample_accession" in loaded.columns

    def test_write_summary_on_pipeline_df(self, tmp_path, pipeline_components, synthetic_ingested_df):
        df = self._run_pipeline(pipeline_components, synthetic_ingested_df.copy())
        summary_path = tmp_path / "summary.csv"
        write_summary(df, summary_path)
        summary = pd.read_csv(summary_path)
        assert "column_name" in summary.columns
        assert "fill_pct" in summary.columns
        assert len(summary) == len(df.columns)

    def test_non_default_index_no_crash(self, pipeline_components, synthetic_ingested_df):
        df = synthetic_ingested_df.copy()
        df.index = [10, 20, 30]
        df = self._run_pipeline(pipeline_components, df)
        assert list(df.index) == [10, 20, 30]
