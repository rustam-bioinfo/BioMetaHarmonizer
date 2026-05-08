"""
Tests for one_health.py.

All tests use synthetic data -- no live NCBI calls are made.
The OneHealthClassifier loads bundled dictionaries from schemas/.
"""

import numpy as np
import pandas as pd
import pytest

from biometaharmonizer.one_health import OneHealthClassifier, discretize_confidence


@pytest.fixture(scope="module")
def clf():
    return OneHealthClassifier()


class TestDiscretizeConfidence:
    @pytest.mark.parametrize("score,expected", [
        (1.0,  "high"),
        (0.85, "high"),
        (0.70, "medium"),
        (0.60, "medium"),
        (0.40, "low"),
        (0.30, "low"),
        (0.10, "unresolved"),
        (0.0,  "unresolved"),
    ])
    def test_thresholds(self, score, expected):
        assert discretize_confidence(score) == expected


class TestClassifySingleField:
    def test_human_blood(self, clf):
        result = clf.classify(pd.Series(["blood"]))
        assert result.iloc[0] == "Human"

    def test_null_returns_unclassified(self, clf):
        result = clf.classify(pd.Series(["missing"]))
        assert result.iloc[0] == "Unclassified"

    def test_nan_returns_unclassified(self, clf):
        result = clf.classify(pd.Series([np.nan]))
        assert result.iloc[0] == "Unclassified"

    def test_soil_returns_environmental(self, clf):
        result = clf.classify(pd.Series(["soil"]))
        assert result.iloc[0] == "Environmental"


class TestClassifyWithConfidence:
    def test_returns_dataframe_with_required_columns(self, clf):
        df = clf.classify_with_confidence(pd.Series(["blood", "soil"]))
        required = {"one_health_category", "one_health_term", "one_health_confidence"}
        assert required.issubset(df.columns)

    def test_confidence_is_float(self, clf):
        df = clf.classify_with_confidence(pd.Series(["blood"]))
        conf = df.iloc[0]["one_health_confidence"]
        assert isinstance(conf, float)
        assert 0.0 <= conf <= 1.0


class TestClassifyMultiField:
    def test_returns_expected_columns(self, clf):
        expected_cols = {
            "one_health_category",
            "one_health_term",
            "one_health_confidence",
            "one_health_evidence_level",
            "one_health_processing",
            "one_health_setting",
            "one_health_source_field",
        }
        df = clf.classify_multi_field(
            isolation_source=pd.Series(["blood"]),
            host=pd.Series(["Homo sapiens"]),
        )
        assert expected_cols.issubset(df.columns)

    def test_human_host_gives_human_category(self, clf):
        df = clf.classify_multi_field(
            host=pd.Series(["Homo sapiens"]),
            isolation_source=pd.Series([np.nan]),
        )
        assert df.iloc[0]["one_health_category"] == "Human"

    def test_all_nan_row_unclassified(self, clf):
        df = clf.classify_multi_field(
            isolation_source=pd.Series([np.nan]),
            host=pd.Series([np.nan]),
        )
        assert df.iloc[0]["one_health_category"] == "Unclassified"

    def test_unknown_field_name_raises_warning(self, clf):
        with pytest.warns(UserWarning, match="unknown field name"):
            clf.classify_multi_field(
                isolation_source=pd.Series(["blood"]),
                bogus_field=pd.Series(["whatever"]),
            )

    def test_no_valid_series_raises_value_error(self, clf):
        with pytest.raises(ValueError, match="no valid series"):
            clf.classify_multi_field(bogus=None)


class TestValidCategoriesOnly:
    """Classifier must never emit 'Lab' as a one_health_category."""
    def test_lab_value_not_emitted_as_category(self, clf):
        inputs = [
            "ATCC 25923",
            "laboratory strain",
            "DSM 799",
            "NCTC 12673",
            "culture collection",
        ]
        result = clf.classify(pd.Series(inputs))
        for val in result:
            assert val != "Lab", (
                f"'Lab' must not be emitted as one_health_category, got {val!r}"
            )


class TestLRUCacheBehavior:
    def test_repeated_calls_return_same_result(self, clf):
        r1 = clf._classify_text("blood")
        r2 = clf._classify_text("blood")
        assert r1["one_health_category"] == r2["one_health_category"]
        assert r1["one_health_confidence"] == r2["one_health_confidence"]
