"""
Tests for one_health.py.

All tests use synthetic data -- no live NCBI calls are made.
The OneHealthClassifier loads bundled dictionaries from schemas/.
"""

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from biometaharmonizer.one_health import (
    OneHealthClassifier,
    _is_institution_host,
    _normalize_host_name,
    _taxonomic_fallback,
    discretize_confidence,
)


# ---------------------------------------------------------------------------
# Module-scoped classifier fixture (shared across all test classes)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def clf():
    return OneHealthClassifier()


# ---------------------------------------------------------------------------
# _load_dictionaries
# ---------------------------------------------------------------------------

class TestLoadDictionaries:
    def test_nonexistent_path_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="not found at the supplied path"):
            OneHealthClassifier(dictionary_path="/no/such/file/dictionaries.json")

    def test_malformed_json_raises_value_error(self, tmp_path):
        bad = tmp_path / "one_health_dictionaries.json"
        bad.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ValueError, match="malformed"):
            OneHealthClassifier(dictionary_path=str(bad))

    def test_missing_required_key_raises_value_error(self, tmp_path):
        minimal = {
            "ontology_map": {},
            "host_to_category": {},
            "unambiguous_human_terms": [],
            "unambiguous_animal_terms": [],
            "ambiguous_specimen_terms": [],
            "synonym_map": {},
            # intentionally omitting tier1_patterns
        }
        p = tmp_path / "one_health_dictionaries.json"
        p.write_text(json.dumps(minimal), encoding="utf-8")
        with pytest.raises(ValueError, match="missing required keys"):
            OneHealthClassifier(dictionary_path=str(p))


# ---------------------------------------------------------------------------
# Classifier initialisation invariants
# ---------------------------------------------------------------------------

class TestClassifierInit:
    def test_all_required_dict_keys_loaded(self, clf):
        required = {
            "ontology_map",
            "host_to_category",
            "unambiguous_human_terms",
            "unambiguous_animal_terms",
            "ambiguous_specimen_terms",
            "synonym_map",
            "tier1_patterns",
        }
        assert required.issubset(clf._dicts.keys())

    def test_valid_categories_do_not_contain_lab(self):
        from biometaharmonizer.one_health import _VALID_CATEGORIES
        assert "Lab" not in _VALID_CATEGORIES

    def test_valid_categories_contains_expected_six(self):
        from biometaharmonizer.one_health import _VALID_CATEGORIES
        assert _VALID_CATEGORIES == frozenset(
            {"Human", "Animal", "Plant", "Food", "Environmental", "Unclassified"}
        )

    def test_tier1_patterns_non_empty(self, clf):
        assert len(clf._TIER1_PATTERNS) > 0

    def test_tier1_patterns_contain_no_lab_category(self, clf):
        categories = [cat for cat, _ in clf._TIER1_PATTERNS]
        assert "Lab" not in categories

    def test_host_to_category_lowercased(self, clf):
        for key in clf._host_to_category:
            assert key == key.lower(), f"non-lowercase key in _host_to_category: {key!r}"


# ---------------------------------------------------------------------------
# discretize_confidence -- boundary values
# ---------------------------------------------------------------------------

class TestDiscretizeConfidence:
    @pytest.mark.parametrize("score,expected", [
        (1.0,   "high"),
        (0.85,  "high"),    # lower boundary of 'high'
        (0.84,  "medium"),
        (0.70,  "medium"),
        (0.60,  "medium"),  # lower boundary of 'medium'
        (0.59,  "low"),
        (0.40,  "low"),
        (0.30,  "low"),     # lower boundary of 'low'
        (0.29,  "unresolved"),
        (0.10,  "unresolved"),
        (0.0,   "unresolved"),
        (-0.01, "unresolved"),
    ])
    def test_thresholds(self, score, expected):
        assert discretize_confidence(score) == expected


# ---------------------------------------------------------------------------
# _expand_abbreviations
# ---------------------------------------------------------------------------

class TestExpandAbbreviations:
    def test_known_abbreviation_expanded(self, clf):
        result = clf._expand_abbreviations("csf")
        assert result.lower() == "cerebrospinal fluid"

    def test_known_abbreviation_case_insensitive(self, clf):
        result = clf._expand_abbreviations("CSF")
        assert result.lower() == "cerebrospinal fluid"

    def test_no_abbreviation_unchanged(self, clf):
        result = clf._expand_abbreviations("blood")
        assert result == "blood"

    def test_abbreviation_with_trailing_punctuation(self, clf):
        # Token "csf," -- the comma is stripped before lookup
        result = clf._expand_abbreviations("csf,")
        assert "cerebrospinal" in result.lower()

    def test_mixed_tokens(self, clf):
        result = clf._expand_abbreviations("csf culture")
        assert "cerebrospinal" in result.lower()


# ---------------------------------------------------------------------------
# _normalize_synonyms
# ---------------------------------------------------------------------------

class TestNormalizeSynonyms:
    def test_known_synonym_replaced(self, clf):
        result = clf._normalize_synonyms("stool sample")
        assert "feces" in result.lower()

    def test_replacement_is_case_insensitive(self, clf):
        result = clf._normalize_synonyms("Stool Sample")
        assert "feces" in result.lower()

    def test_no_synonym_unchanged(self, clf):
        result = clf._normalize_synonyms("blood")
        assert result == "blood"

    def test_homo_sapien_normalized(self, clf):
        result = clf._normalize_synonyms("homo sapien")
        assert result.lower() == "homo sapiens"


# ---------------------------------------------------------------------------
# _taxonomic_fallback
# ---------------------------------------------------------------------------

class TestTaxonomicFallback:
    def test_trinomial_hits_binomial(self, clf):
        # 'equus ferus caballus' not in dict; 'equus caballus' is
        result = _taxonomic_fallback("equus ferus caballus", clf._host_to_category)
        assert result == "Animal"

    def test_binomial_hits_genus(self, clf):
        # Build a minimal dict where only the genus is present
        minimal = {"gallus": "Animal"}
        result = _taxonomic_fallback("gallus gallus", minimal)
        assert result == "Animal"

    def test_single_token_returns_none(self, clf):
        result = _taxonomic_fallback("homo", clf._host_to_category)
        assert result is None

    def test_five_tokens_returns_none(self, clf):
        result = _taxonomic_fallback("a b c d e", clf._host_to_category)
        assert result is None

    def test_digit_token_returns_none(self, clf):
        result = _taxonomic_fallback("sus scrofa 9825", clf._host_to_category)
        assert result is None

    def test_no_hit_returns_none(self, clf):
        result = _taxonomic_fallback("fictus nomenspecies", clf._host_to_category)
        assert result is None


# ---------------------------------------------------------------------------
# _is_institution_host
# ---------------------------------------------------------------------------

class TestIsInstitutionHost:
    def test_institution_keyword_detected(self):
        assert _is_institution_host("Harvard University Hospital") is True

    def test_laboratory_keyword_detected(self):
        assert _is_institution_host("National Laboratory of Microbiology") is True

    def test_comma_address_detected(self):
        assert _is_institution_host("John Smith Jr, New York NY") is True

    def test_normal_host_not_institution(self):
        assert _is_institution_host("Homo sapiens") is False

    def test_simple_animal_not_institution(self):
        assert _is_institution_host("Sus scrofa") is False


# ---------------------------------------------------------------------------
# _normalize_host_name
# ---------------------------------------------------------------------------

class TestNormalizeHostName:
    def test_subsp_suffix_stripped(self):
        assert _normalize_host_name("Sus scrofa subsp. domesticus") == "sus scrofa"

    def test_var_suffix_stripped(self):
        result = _normalize_host_name("Triticum aestivum var. spelta")
        assert result == "triticum aestivum"

    def test_sp_suffix_stripped(self):
        result = _normalize_host_name("Bacillus sp.")
        assert result == "bacillus"

    def test_parenthetical_stripped(self):
        result = _normalize_host_name("Gallus gallus (Linnaeus 1758)")
        assert "linnaeus" not in result
        assert "gallus" in result

    def test_result_is_lowercased(self):
        result = _normalize_host_name("Homo Sapiens")
        assert result == result.lower()

    def test_idempotent_on_clean_name(self):
        result1 = _normalize_host_name("bos taurus")
        result2 = _normalize_host_name(result1)
        assert result1 == result2


# ---------------------------------------------------------------------------
# classify -- output shape and index
# ---------------------------------------------------------------------------

class TestClassifyOutputShape:
    def test_returns_series(self, clf):
        result = clf.classify(pd.Series(["blood", "soil"]))
        assert isinstance(result, pd.Series)

    def test_length_preserved(self, clf):
        s = pd.Series(["blood", "soil", "hospital"])
        assert len(clf.classify(s)) == len(s)

    def test_index_preserved(self, clf):
        s = pd.Series(["blood", "soil"], index=[10, 20])
        result = clf.classify(s)
        assert list(result.index) == [10, 20]

    def test_empty_series_returns_empty_series(self, clf):
        result = clf.classify(pd.Series([], dtype=object))
        assert len(result) == 0


# ---------------------------------------------------------------------------
# classify single-field smoke tests
# ---------------------------------------------------------------------------

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

    def test_chicken_returns_animal(self, clf):
        result = clf.classify(pd.Series(["chicken"]))
        assert result.iloc[0] == "Animal"

    def test_milk_returns_food(self, clf):
        result = clf.classify(pd.Series(["milk"]))
        assert result.iloc[0] == "Food"

    def test_tomato_host_returns_plant(self, clf):
        result = clf.classify(pd.Series(["tomato"]))
        assert result.iloc[0] == "Plant"


# ---------------------------------------------------------------------------
# Underscore normalisation
# ---------------------------------------------------------------------------

class TestUnderscoreNormalization:
    def test_environmental_soil_underscore(self, clf):
        result = clf._classify_text("Environmental_soil")
        assert result["one_health_category"] == "Environmental"

    def test_blood_blood_underscore_synonym(self, clf):
        # 'blood_blood' is in synonym_map -> 'blood'
        result = clf._classify_text("blood_blood")
        assert result["one_health_category"] in ("Human", "Unclassified")


# ---------------------------------------------------------------------------
# Processing and setting extraction
# ---------------------------------------------------------------------------

class TestProcessingAndSetting:
    def test_processing_term_extracted(self, clf):
        result = clf._classify_text("frozen blood")
        assert pd.notna(result["one_health_processing"])
        assert "frozen" in str(result["one_health_processing"]).lower()

    def test_setting_term_extracted(self, clf):
        result = clf._classify_text("hospital blood")
        # After stripping the setting term, the remaining text is still classified;
        # the setting field should be populated.
        assert pd.notna(result["one_health_setting"]) or result["one_health_category"] == "Human"

    def test_null_value_has_no_processing(self, clf):
        result = clf._classify_text(np.nan)
        assert pd.isna(result["one_health_processing"])


# ---------------------------------------------------------------------------
# Animal-origin pattern  ("a <specimen> of <animal> origin")
# ---------------------------------------------------------------------------

class TestAnimalOriginPattern:
    def test_bovine_origin(self, clf):
        result = clf._classify_text("a blood sample of bovine origin")
        assert result["one_health_category"] == "Animal"

    def test_swine_origin(self, clf):
        result = clf._classify_text("a feces sample of swine origin")
        assert result["one_health_category"] == "Animal"

    def test_origin_confidence_is_high(self, clf):
        result = clf._classify_text("a urine sample of bovine origin")
        assert result["one_health_confidence"] >= 0.85


# ---------------------------------------------------------------------------
# Species-in-parentheses pattern in classify_multi_field
# ---------------------------------------------------------------------------

class TestSpeciesInParens:
    def test_feces_dog_species(self, clf):
        df = clf.classify_multi_field(
            isolation_source=pd.Series(["feces (Canis lupus familiaris)"]),
        )
        assert df.iloc[0]["one_health_category"] == "Animal"

    def test_kidney_homo_sapiens(self, clf):
        df = clf.classify_multi_field(
            isolation_source=pd.Series(["kidney (Homo sapiens)"]),
        )
        assert df.iloc[0]["one_health_category"] == "Human"


# ---------------------------------------------------------------------------
# classify_with_confidence
# ---------------------------------------------------------------------------

class TestClassifyWithConfidence:
    def test_returns_dataframe_with_required_columns(self, clf):
        df = clf.classify_with_confidence(pd.Series(["blood", "soil"]))
        required = {"one_health_category", "one_health_term", "one_health_confidence"}
        assert required.issubset(df.columns)

    def test_confidence_is_float_in_range(self, clf):
        df = clf.classify_with_confidence(pd.Series(["blood"]))
        conf = df.iloc[0]["one_health_confidence"]
        assert isinstance(conf, float)
        assert 0.0 <= conf <= 1.0

    def test_non_default_index_preserved(self, clf):
        s = pd.Series(["blood", "soil"], index=[100, 200])
        df = clf.classify_with_confidence(s)
        assert list(df.index) == [100, 200]


# ---------------------------------------------------------------------------
# classify_multi_field
# ---------------------------------------------------------------------------

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

    def test_evidence_level_column_populated(self, clf):
        df = clf.classify_multi_field(
            isolation_source=pd.Series(["blood", "soil", np.nan]),
        )
        valid = {"high", "medium", "low", "unresolved"}
        for val in df["one_health_evidence_level"]:
            assert val in valid, f"unexpected evidence_level: {val!r}"

    def test_corroboration_increases_confidence(self, clf):
        # Two agreeing fields should yield higher confidence than one alone
        df_two = clf.classify_multi_field(
            isolation_source=pd.Series(["blood"]),
            host=pd.Series(["Homo sapiens"]),
        )
        df_one = clf.classify_multi_field(
            isolation_source=pd.Series(["blood"]),
            host=pd.Series([np.nan]),
        )
        assert df_two.iloc[0]["one_health_confidence"] >= df_one.iloc[0]["one_health_confidence"]

    def test_isolation_source_priority_over_env_broad_scale(self, clf):
        # isolation_source (weight 1.0) should win over env_broad_scale (weight 0.5)
        df = clf.classify_multi_field(
            isolation_source=pd.Series(["blood"]),
            env_broad_scale=pd.Series(["soil"]),
        )
        # blood -> Human; soil -> Environmental
        # domain field takes priority
        assert df.iloc[0]["one_health_source_field"] in ("isolation_source", "host")

    def test_series_index_alignment(self, clf):
        # Two series with same index values but different order; reindex to first's index
        iso = pd.Series(["blood", "soil"], index=[1, 2])
        host = pd.Series(["Homo sapiens", np.nan], index=[1, 2])
        df = clf.classify_multi_field(isolation_source=iso, host=host)
        assert list(df.index) == [1, 2]

    def test_underscore_in_isolation_source(self, clf):
        df = clf.classify_multi_field(
            isolation_source=pd.Series(["Environmental_soil"]),
        )
        assert df.iloc[0]["one_health_category"] == "Environmental"

    def test_non_default_index_preserved(self, clf):
        s = pd.Series(["blood"], index=[42])
        df = clf.classify_multi_field(isolation_source=s)
        assert list(df.index) == [42]


# ---------------------------------------------------------------------------
# classify_joint
# ---------------------------------------------------------------------------

class TestClassifyJoint:
    def test_returns_series(self, clf):
        result = clf.classify_joint(
            pd.Series(["blood"]),
            pd.Series(["Homo sapiens"]),
        )
        assert isinstance(result, pd.Series)

    def test_mismatched_indices_raise_value_error(self, clf):
        iso = pd.Series(["blood"], index=[0])
        host = pd.Series(["Homo sapiens"], index=[99])
        with pytest.raises(ValueError):
            clf.classify_joint(iso, host)

    def test_result_category_is_valid(self, clf):
        from biometaharmonizer.one_health import _VALID_CATEGORIES
        result = clf.classify_joint(
            pd.Series(["blood", "soil"]),
            pd.Series(["Homo sapiens", np.nan]),
        )
        for val in result:
            assert val in _VALID_CATEGORIES


# ---------------------------------------------------------------------------
# Lab category must never be emitted
# ---------------------------------------------------------------------------

class TestValidCategoriesOnly:
    def test_lab_value_not_emitted_as_category(self, clf):
        inputs = [
            "ATCC 25923",
            "laboratory strain",
            "DSM 799",
            "NCTC 12673",
            "culture collection",
            "liquid culture",
            "bacterial culture",
        ]
        result = clf.classify(pd.Series(inputs))
        for val in result:
            assert val != "Lab", (
                f"'Lab' must not be emitted as one_health_category, got {val!r}"
            )

    def test_lab_not_emitted_via_multi_field(self, clf):
        df = clf.classify_multi_field(
            isolation_source=pd.Series(["laboratory strain", "cell culture"]),
        )
        for val in df["one_health_category"]:
            assert val != "Lab"


# ---------------------------------------------------------------------------
# LRU cache behaviour
# ---------------------------------------------------------------------------

class TestLRUCacheBehavior:
    def test_repeated_calls_return_same_result(self, clf):
        r1 = clf._classify_text("blood")
        r2 = clf._classify_text("blood")
        assert r1["one_health_category"] == r2["one_health_category"]
        assert r1["one_health_confidence"] == r2["one_health_confidence"]

    def test_cache_returns_same_object_identity(self, clf):
        r1 = clf._classify_text("soil")
        r2 = clf._classify_text("soil")
        # LRU cache returns the exact same dict object for cached calls
        assert r1 is r2
