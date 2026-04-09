import pytest
import numpy as np
import pandas as pd
from biometaharmonizer.one_health import OneHealthClassifier


@pytest.fixture
def clf():
    return OneHealthClassifier()


# --- Human category ---

class TestHuman:

    def test_real_bcereus_human_blood(self, clf):
        assert clf._classify_single("Human blood") == "Human"

    def test_real_bcereus_dental_plaque(self, clf):
        assert clf._classify_single("dental plaque") == "Human"

    def test_real_bcereus_hospital(self, clf):
        assert clf._classify_single("hospital") == "Human"

    def test_urine(self, clf):
        assert clf._classify_single("urine") == "Human"

    def test_wound_swab(self, clf):
        assert clf._classify_single("wound swab") == "Human"

    def test_homo_sapiens_host(self, clf):
        assert clf._classify_single("Homo sapiens") == "Human"

    def test_clinical_sample(self, clf):
        assert clf._classify_single("clinical isolate") == "Human"

    def test_nasopharyngeal_swab(self, clf):
        assert clf._classify_single("nasopharyngeal swab") == "Human"

    def test_sputum(self, clf):
        assert clf._classify_single("sputum") == "Human"


# --- Animal category ---

class TestAnimal:

    def test_real_bcereus_insect_colony(self, clf):
        assert clf._classify_single(
            "Laboratory reared workers from wild caught queens"
        ) == "Animal"

    def test_bovine_rumen(self, clf):
        assert clf._classify_single("bovine rumen") == "Animal"

    def test_chicken_gut(self, clf):
        assert clf._classify_single("chicken gut") == "Animal"

    def test_wild_caught_resolves_before_lab(self, clf):
        assert clf._classify_single("wild caught insect") == "Animal"

    def test_carcass(self, clf):
        assert clf._classify_single("carcass") == "Animal"

    def test_bovine_blood_not_human(self, clf):
        """bovine blood should classify as Animal, not Human."""
        assert clf._classify_single("bovine blood") == "Animal"

    def test_animal_blood_not_human(self, clf):
        assert clf._classify_single("animal blood") == "Animal"

    def test_pig_blood_not_human(self, clf):
        assert clf._classify_single("pig blood") == "Animal"


# --- Food category ---

class TestFood:

    def test_dairy_milk(self, clf):
        assert clf._classify_single("dairy milk") == "Food"

    def test_fermented_food(self, clf):
        assert clf._classify_single("fermented soybean") == "Food"

    def test_meat(self, clf):
        assert clf._classify_single("raw meat") == "Food"

    def test_real_bcereus_ice_cream(self, clf):
        assert clf._classify_single("ice cream") == "Food"

    def test_real_bcereus_pasta(self, clf):
        assert clf._classify_single("Pasta") == "Food"

    def test_real_bcereus_chinese_sausage(self, clf):
        assert clf._classify_single("Chinese sausage") == "Food"

    def test_real_bcereus_grain_products(self, clf):
        assert clf._classify_single("grain products") == "Food"

    def test_vitamin_b2_feed_additive(self, clf):
        assert clf._classify_single("Vitamin B2 feed additive") == "Food"


# --- Environmental category ---

class TestEnvironmental:

    def test_soil(self, clf):
        assert clf._classify_single("agricultural soil") == "Environmental"

    def test_river_water(self, clf):
        assert clf._classify_single("river water") == "Environmental"

    def test_rhizosphere(self, clf):
        assert clf._classify_single("rhizosphere soil") == "Environmental"

    def test_biofilm(self, clf):
        assert clf._classify_single("surface biofilm") == "Environmental"

    def test_real_bcereus_farm(self, clf):
        assert clf._classify_single("farm") == "Environmental"

    def test_environmental_swab(self, clf):
        """environmental swab should classify as Environmental, not Human."""
        assert clf._classify_single("environmental swab") == "Environmental"

    def test_env_swab(self, clf):
        """env swab should classify as Environmental, not Human."""
        assert clf._classify_single("env swab") == "Environmental"


# --- Lab category ---

class TestLab:

    def test_atcc_reference(self, clf):
        assert clf._classify_single("ATCC 14579") == "Lab"

    def test_type_strain(self, clf):
        assert clf._classify_single("type strain") == "Lab"

    def test_laboratory_culture(self, clf):
        assert clf._classify_single("laboratory culture") == "Lab"

    def test_real_bcereus_dna(self, clf):
        assert clf._classify_single("DNA") == "Lab"

    def test_real_bcereus_whole_organism(self, clf):
        assert clf._classify_single("whole organism") == "Lab"

    def test_in_vitro(self, clf):
        assert clf._classify_single("in vitro") == "Lab"


# --- Null strings now return NaN, not Unclassified ---

class TestNullAndUnclassified:

    def test_nan_returns_nan(self, clf):
        assert pd.isna(clf._classify_single(np.nan))

    def test_not_collected_returns_nan(self, clf):
        assert pd.isna(clf._classify_single("not collected"))

    def test_not_applicable_returns_nan(self, clf):
        assert pd.isna(clf._classify_single("not applicable"))

    def test_missing_returns_nan(self, clf):
        assert pd.isna(clf._classify_single("missing"))

    def test_unknown_returns_nan(self, clf):
        assert pd.isna(clf._classify_single("unknown"))

    def test_truly_ambiguous_returns_unclassified(self, clf):
        assert clf._classify_single("completely_novel_source_xyz") == "Unclassified"

    def test_empty_string_returns_unclassified(self, clf):
        assert clf._classify_single("") == "Unclassified"


# --- Series-level classify() ---

class TestClassifySeries:

    def test_real_bcereus_isolation_source_column(self, clf):
        s = pd.Series([
            "dental plaque",
            "Human blood",
            "hospital",
            "Laboratory reared workers from wild caught queens"
        ])
        result = clf.classify(s)
        assert result[0] == "Human"
        assert result[1] == "Human"
        assert result[2] == "Human"
        assert result[3] == "Animal"

    def test_null_strings_return_nan_not_unclassified(self, clf):
        s = pd.Series(["not collected", "not applicable", "missing", "unknown"])
        result = clf.classify(s)
        assert all(pd.isna(result))

    def test_output_is_series(self, clf):
        s = pd.Series(["human blood", "soil", np.nan])
        result = clf.classify(s)
        assert isinstance(result, pd.Series)
        assert len(result) == 3

    def test_mixed_categories(self, clf):
        s = pd.Series(["human blood", "soil", "raw meat", "ATCC 14579", "bovine"])
        result = clf.classify(s)
        assert result[0] == "Human"
        assert result[1] == "Environmental"
        assert result[2] == "Food"
        assert result[3] == "Lab"
        assert result[4] == "Animal"


# --- classify_joint() ---

class TestClassifyJoint:

    def test_fallback_to_host(self, clf):
        iso = pd.Series([np.nan, "soil"])
        host = pd.Series(["human", "Bos taurus"])
        result = clf.classify_joint(iso, host)
        assert result[0] == "Human"
        assert result[1] == "Environmental"

    def test_unclassified_fallback_to_host(self, clf):
        iso = pd.Series(["completely_novel_source_xyz"])
        host = pd.Series(["Homo sapiens"])
        result = clf.classify_joint(iso, host)
        assert result[0] == "Human"

    def test_iso_takes_priority(self, clf):
        iso = pd.Series(["soil"])
        host = pd.Series(["Homo sapiens"])
        result = clf.classify_joint(iso, host)
        assert result[0] == "Environmental"


# --- classify_with_confidence() ---

class TestClassifyWithConfidence:

    def test_returns_dataframe_with_correct_columns(self, clf):
        s = pd.Series(["blood culture", "soil", np.nan])
        result = clf.classify_with_confidence(s)
        assert isinstance(result, pd.DataFrame)
        assert "one_health_category" in result.columns
        assert "one_health_term" in result.columns
        assert "one_health_confidence" in result.columns

    def test_strong_match_confidence(self, clf):
        s = pd.Series(["agricultural soil"])
        result = clf.classify_with_confidence(s)
        assert result["one_health_category"][0] == "Environmental"
        assert result["one_health_confidence"][0] == 1.0
        assert pd.notna(result["one_health_term"][0])

    def test_unclassified_confidence(self, clf):
        s = pd.Series(["completely_novel_source_xyz"])
        result = clf.classify_with_confidence(s)
        assert result["one_health_category"][0] == "Unclassified"
        assert result["one_health_confidence"][0] == 0.0

    def test_nan_input_confidence(self, clf):
        s = pd.Series([np.nan])
        result = clf.classify_with_confidence(s)
        assert pd.isna(result["one_health_category"][0])
        assert result["one_health_confidence"][0] == 0.0
