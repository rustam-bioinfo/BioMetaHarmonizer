"""CLI wiring tests with synthetic data and mocked pipeline dependencies."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from biometaharmonizer.cli import _build_parser, _run


def arguments(tmp_path, enabled=False, multi=False):
    prefix = chr(45) * 2
    argv = ["run", prefix + "input", "SAMN001", prefix + "email", "real@lab.org",
            prefix + "output", str(tmp_path / "result.csv")]
    if multi:
        argv += [prefix + "format", "csv", "tsv", "excel"]
    if enabled:
        argv.append(prefix + "postprocess")
    return _build_parser().parse_args(argv)


@pytest.fixture
def pipeline(monkeypatch):
    data = pd.DataFrame({"biosample_accession": ["SAMN001"], "host": ["Homo sapiens"]})
    ingest = MagicMock(return_value=data)
    writer, summary = MagicMock(), MagicMock()
    classifier = MagicMock()
    classifier.return_value.classify_multi_field.return_value = pd.DataFrame({"one_health_category": ["Human"]})

    def transform(df):
        assert df.iloc[0]["one_health_category"] == "Human"
        result = df.copy()
        result["decimalLatitude"] = 0.0
        result["decimalLongitude"] = 0.0
        result["host_health_state"] = "disease"
        return result, pd.DataFrame({"coordinate_status": ["parsed"]})

    processor = MagicMock(side_effect=transform)
    monkeypatch.setattr("biometaharmonizer.ingestion.set_email", MagicMock())
    monkeypatch.setattr("biometaharmonizer.ingestion.ingest", ingest)
    monkeypatch.setattr("biometaharmonizer.one_health.OneHealthClassifier", classifier)
    monkeypatch.setattr("biometaharmonizer.output.write", writer)
    monkeypatch.setattr("biometaharmonizer.output.write_summary", summary)
    monkeypatch.setattr("biometaharmonizer.postprocess.postprocess", processor)
    return data, ingest, writer, summary, processor


def test_parser_defaults_and_flag(tmp_path):
    args = arguments(tmp_path)
    assert args.postprocess is False
    assert args.postprocess_report is None
    assert arguments(tmp_path, True).postprocess is True


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("multi", [False, True])
def test_once_before_all_formats(tmp_path, pipeline, enabled, multi):
    data, _, writer, _, processor = pipeline
    args = arguments(tmp_path, enabled, multi)
    assert _run(args) == 0
    assert processor.call_count == int(enabled)
    assert writer.call_count == (3 if multi else 1)
    assert [call.kwargs["fmt"] for call in writer.call_args_list] == (["csv", "tsv", "excel"] if multi else ["csv"])
    first = writer.call_args_list[0].args[0]
    for call in writer.call_args_list:
        assert call.args[0] is first
        assert ("decimalLatitude" in first.columns) == enabled
        assert first.iloc[0]["one_health_category"] == "Human"
    assert "decimalLatitude" not in data.columns


def test_report_requires_flag_before_ingestion(tmp_path, pipeline):
    args = arguments(tmp_path)
    args.postprocess_report = str(tmp_path / "qc.csv")
    assert _run(args) == 1
    pipeline[1].assert_not_called()


@pytest.mark.parametrize("target", ["output", "summary", "input", "multiformat"])
def test_report_collision_before_ingestion(tmp_path, pipeline, target):
    args = arguments(tmp_path, True, True)
    if target == "output":
        args.postprocess_report = args.output
    elif target == "multiformat":
        args.postprocess_report = str(tmp_path / "result.tsv")
    elif target == "summary":
        args.summary = args.postprocess_report = str(tmp_path / "summary.csv")
    else:
        path = tmp_path / "ids.txt"
        path.write_text("SAMN001\n", encoding="utf-8")
        args.input = args.postprocess_report = str(path)
    assert _run(args) == 1
    pipeline[1].assert_not_called()


def test_report_and_summary(tmp_path, pipeline):
    args = arguments(tmp_path, True, True)
    prefix = chr(45) * 2
    parsed = _build_parser().parse_args([
        "run", prefix + "input", "SAMN001", prefix + "email", "real@lab.org",
        prefix + "output", args.output, prefix + "postprocess",
        prefix + "postprocess-report", str(tmp_path / "qc.csv"),
    ])
    assert parsed.postprocess_report.endswith("qc.csv")
    args.postprocess_report = parsed.postprocess_report
    args.summary = str(tmp_path / "summary.csv")
    assert _run(args) == 0
    writer, summary = pipeline[2:4]
    assert writer.call_count == 4
    assert "coordinate_status" in writer.call_args_list[0].args[0].columns
    assert writer.call_args_list[0].args[1] == args.postprocess_report
    assert "decimalLatitude" in summary.call_args.args[0].columns


def test_processor_failure_stops_output(tmp_path, pipeline):
    args = arguments(tmp_path, True)
    pipeline[4].side_effect = ValueError("failed")
    assert _run(args) == 2
    pipeline[2].assert_not_called()
    pipeline[3].assert_not_called()


def test_report_write_failure_stops_output(tmp_path, pipeline):
    args = arguments(tmp_path, True)
    args.postprocess_report = str(tmp_path / "qc.csv")
    pipeline[2].side_effect = OSError("disk full")
    assert _run(args) == 2
    assert pipeline[2].call_count == 1
    pipeline[3].assert_not_called()


def test_older_namespace_without_new_flags(tmp_path, pipeline):
    args = arguments(tmp_path)
    del args.postprocess
    del args.postprocess_report
    assert _run(args) == 0
    pipeline[4].assert_not_called()
