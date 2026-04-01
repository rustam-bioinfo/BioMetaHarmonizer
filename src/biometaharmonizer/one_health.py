import pandas as pd
import numpy as np
import re


class OneHealthClassifier:
    """
    Module 5: One Health Categorization.
    Classifies isolation_source and host fields into
    standardized One Health tiers using Regex (Tier 1)
    and a placeholder for NLP extension (Tier 2).
    """

    TIER1_PATTERNS = {
        "Human": re.compile(
            r"human|patient|clinical|homo sapiens|person|blood|urine|sputum|wound|stool|feces|fecal",
            re.IGNORECASE
        ),
        "Animal": re.compile(
            r"bovine|cattle|pig|swine|poultry|chicken|sheep|horse|dog|cat|rodent|mouse|rat|bird|fish|animal",
            re.IGNORECASE
        ),
        "Food": re.compile(
            r"food|meat|milk|cheese|vegetable|fruit|poultry product|dairy|egg|seafood|water supply",
            re.IGNORECASE
        ),
        "Environmental": re.compile(
            r"soil|water|river|lake|sewage|wastewater|air|plant|rhizosphere|sediment|environment",
            re.IGNORECASE
        ),
        "Lab": re.compile(
            r"lab|laboratory|culture|atcc|reference strain|type strain|synthetic",
            re.IGNORECASE
        )
    }

    def classify(self, series):
        return series.apply(self._classify_single)

    def _classify_single(self, value):
        if pd.isna(value):
            return np.nan
        value = str(value).strip()
        for category, pattern in self.TIER1_PATTERNS.items():
            if pattern.search(value):
                return category
        return "Unclassified"
