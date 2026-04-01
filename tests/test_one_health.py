import pytest
import numpy as np
import pandas as pd
from biometaharmonizer.one_health import OneHealthClassifier


@pytest.fixture
def clf():
    return OneHealthClassifier()


# ─── Human category ──────────────────────────────────────────────────────────────

class TestHuman:

    def test_real_bcereus_human_blood(self, clf):
        assert clf._classify_single("Human blood") == "Human"

    def test_real_bcereus_dental_plaque(self, clf):
        assert clf._classify_single("dental plaque") == "Human"

    def test_real_bcereus_hospital(self, clf):
        assert clf._classify_single("hospital") == "Human"

    def test_blood_lowercase(self, clf):
        assert clf._classify_single("blood culture") == "Human"

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


# ─── Animal category ─────────────────────────────────────────────────────────────

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
        result = clf._classify_single("wild caught insect")
        assert result == "Animal"


# ─── Food category ───────────────────────────────────────────────────────────────

class TestFood:

    def test_dairy_milk(self, clf):
        assert clf._classify_single("dairy milk") == "Food"

    def test_fermented_food(self, clf):
        assert clf._classify_single("fermented soybean") == "Food"

    def test_meat(self, clf):
        assert clf._classify_single("raw meat") == "Food"


# ─── Environmental category ───────────────────────────────────────────────────

class TestEnvironmental:

    def test_soil(self, clf):
        assert clf._classify_single("agricultural soil") == "Environmental"

    def test_river_water(self, clf):
        assert clf._classify_single("river water") == "Environmental"

    def test_rhizosphere(self, clf):
        assert clf._classify_single("rhizosphere soil") == "Environmental"

    def test_biofilm(self, clf):
        assert clf._classify_single("surface biofilm") == "Environmental"


# ─── Lab category ────────────────────────────────────────────────────────────────

class TestLab:

    def test_atcc_reference(self, clf):
        assert clf._classify_single("ATCC 14579") == "Lab"

    def test_type_strain(self, clf):
        assert clf._classify_single("type strain") == "Lab"

    def test_laboratory_culture(self, clf):
        assert clf._classify_single("laboratory culture") == "Lab"


# ─── Null and Unclassified ──────────────────────────────────────────────────────

class TestNullAndUnclassified:

    def test_nan_returns_nan(self, clf):
        assert pd.isna(clf._classify_single(np.nan))

    def test_truly_ambiguous_returns_unclassified(self, clf):
        assert clf._classify_single("unknown source") == "Unclassified"

    def test_empty_string_returns_unclassified(self, clf):
        assert clf._classify_single("") == "Unclassified"


# ─── Series-level classify() ────────────────────────────────────────────────────

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

    def test_output_is_series(self, clf):
        s = pd.Series(["blood", "soil", np.nan])
        result = clf.classify(s)
        assert isinstance(result, pd.Series)
        assert len(result) == 3

    def test_mixed_categories(self, clf):
        s = pd.Series(["blood", "soil", "raw meat", "ATCC 14579", "bovine"])
        result = clf.classify(s)
        assert result[0] == "Human"
        assert result[1] == "Environmental"
        assert result[2] == "Food"
        assert result[3] == "Lab"
        assert result[4] == "Animal"
