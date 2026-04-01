import pandas as pd
import numpy as np
import re


class OneHealthClassifier:
    """
    Module 5: One Health Categorization.
    Classifies isolation_source and host fields into
    standardized One Health tiers using Regex (Tier 1)
    and a placeholder for NLP extension (Tier 2).

    Categories (in priority order):
        Human, Animal, Food, Environmental, Lab, Unclassified

    Priority order matters: Animal is checked before Lab so that
    'laboratory reared workers from wild caught queens' resolves
    to Animal rather than Lab.
    """

    TIER1_PATTERNS = {
        "Human": re.compile(
            r"human|patient|clinical|homo sapiens|person|"
            r"blood|urine|sputum|wound|stool|feces|fecal|"
            r"dental|plaque|biopsy|serum|plasma|csf|cerebrospinal|"
            r"nasopharyngeal|throat|swab|abscess|hospital",
            re.IGNORECASE
        ),
        "Animal": re.compile(
            r"bovine|cattle|pig|swine|poultry|chicken|sheep|horse|"
            r"dog|cat|rodent|mouse|rat|bird|fish|animal|"
            r"insect|bee|ant|wasp|queen|worker|colony|larva|larvae|"
            r"wild.caught|reared|invertebrate|arthropod",
            re.IGNORECASE
        ),
        "Food": re.compile(
            r"food|meat|milk|cheese|vegetable|fruit|poultry product|"
            r"dairy|egg|seafood|water supply|spice|grain|cereal|"
            r"flour|bread|fermented|beverage",
            re.IGNORECASE
        ),
        "Environmental": re.compile(
            r"soil|water|river|lake|sewage|wastewater|air|plant|"
            r"rhizosphere|sediment|environment|dust|biofilm|"
            r"compost|manure|surface|outdoor|indoor|cave|sand",
            re.IGNORECASE
        ),
        "Lab": re.compile(
            r"\blab\b|laboratory|culture|atcc|reference strain|"
            r"type strain|synthetic|in vitro",
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
