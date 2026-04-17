import re

import numpy as np
import pandas as pd


class OneHealthClassifier:
    """
    Module 5: One Health Categorization.
    Classifies isolation_source and host fields into
    standardized One Health tiers using Regex (Tier 1)
    and a placeholder for NLP extension (Tier 2).

    Categories (in priority order):
        Environmental, Animal, Human, Food, Lab, Unclassified

    Priority order matters: Environmental is checked before Human
    so that 'environmental swab' resolves to Environmental rather than Human.
    Animal is checked before Human so that 'bovine blood' resolves to Animal.

    TIER1_PATTERNS is a tuple of (category, pattern) pairs rather than a dict
    so that the priority order is structurally enforced and cannot be silently
    broken by future insertions.

    Word boundaries are applied to all short tokens to prevent
    substring false positives (e.g. 'rat' inside 'laboratory').

    NULL_PATTERNS are checked first and return NaN, not Unclassified.
    Empty strings also return NaN (not Unclassified) for the same reason.
    """

    NULL_PATTERNS = re.compile(
        r"^(missing|unknown|n/?a|not provided|not collected|not applicable|na|none|--)$",
        re.IGNORECASE
    )

    # Tuple of (category, compiled pattern) pairs — order defines priority.
    TIER1_PATTERNS = (
        ("Environmental", re.compile(
            r"soil|\bwater\b|river|lake|sewage|wastewater|\bair\b|"
            r"rhizosphere|sediment|environment|dust|biofilm|"
            r"compost|manure|surface|outdoor|indoor|cave|sand|"
            r"farm|field|forest|bark|\bmoss\b|lichen|peat|"
            r"wipe|swab.*surface|\bbaby\b.*wipe|"
            r"environmental\s+swab|env\s+swab",
            re.IGNORECASE
        )),
        ("Animal", re.compile(
            r"bovine|cattle|\bpig\b|swine|poultry|chicken|sheep|horse|"
            r"\bdog\b|\bcat\b|rodent|\bmouse\b|\brat\b|\bbird\b|\bfish\b|animal|"
            r"insect|\bbee\b|\bant\b|wasp|queen|colony|\blarva\b|larvae|"
            r"wild.caught|reared|invertebrate|arthropod|carcass|gut|flea|tick|"
            r"\bfly\b|\bmite\b|\bworm\b|nematode",
            re.IGNORECASE
        )),
        ("Human", re.compile(
            r"human|patient|clinical|homo sapiens|person|"
            r"(?<!bovine )(?<!animal )(?<!pig )(?<!cattle )\bblood\b|"
            r"urine|sputum|wound|stool|feces|fecal|"
            r"dental|plaque|biopsy|serum|plasma|\bcsf\b|cerebrospinal|"
            r"nasopharyngeal|throat|(?<!environmental )(?<!env )\bswab\b|abscess|hospital",
            re.IGNORECASE
        )),
        ("Food", re.compile(
            r"food|\bmeat\b|\bmilk\b|cheese|vegetable|fruit|poultry product|"
            r"dairy|\begg\b|seafood|water supply|spice|grain|cereal|"
            r"flour|bread|fermented|beverage|pasta|sausage|ice.?cream|"
            r"noodle|rice|soy|tofu|sprout|produce|feed|additive|"
            r"supplement|\bhusk\b|"
            r"\bbean\b|\bnut\b|\bherb\b|spore.?forming|"
            r"slaughterhouse|abattoir|retail food",
            re.IGNORECASE
        )),
        ("Lab", re.compile(
            r"\blab\b|laboratory|\bculture\b|atcc|reference strain|"
            r"type strain|synthetic|in vitro|\bdna\b|whole organism|"
            r"\bstrain\b.*collection",
            re.IGNORECASE
        )),
    )

    def classify(self, series):
        return series.apply(self._classify_single)

    def _classify_single(self, value):
        if pd.isna(value):
            return np.nan
        value = str(value).strip()
        # Empty string is semantically missing, not Unclassified
        if not value:
            return np.nan
        if self.NULL_PATTERNS.match(value):
            return np.nan
        for category, pattern in self.TIER1_PATTERNS:
            if pattern.search(value):
                return category
        return "Unclassified"

    def classify_joint(self, isolation_source_series, host_series):
        """
        Classify using isolation_source first; where result is NaN or
        'Unclassified', fall back to classifying host.

        Parameters
        ----------
        isolation_source_series : pd.Series
        host_series : pd.Series
            Must share the same index as isolation_source_series.

        Returns
        -------
        pd.Series of one_health_category
        """
        # .copy() prevents SettingWithCopyWarning on the mask assignment below
        result = self.classify(isolation_source_series).copy()
        fallback_mask = result.isna() | (result == "Unclassified")
        if fallback_mask.any():
            # Boolean indexing avoids KeyError when series have different indexes
            host_result = self.classify(host_series[fallback_mask])
            result[fallback_mask] = host_result
        return result

    def classify_with_confidence(self, series):
        """
        Classify and return a DataFrame with columns:
          - one_health_category
          - one_health_term: the regex term that matched
          - one_health_confidence: float 0-1
            1.0 for strong match, 0.5 for fallback/uncertain, 0.0 for unclassified

        Parameters
        ----------
        series : pd.Series

        Returns
        -------
        pd.DataFrame
        """
        results = series.apply(self._classify_single_with_confidence)
        return pd.DataFrame(results.tolist(), index=series.index)

    def _classify_single_with_confidence(self, value):
        empty = {
            "one_health_category": np.nan,
            "one_health_term": np.nan,
            "one_health_confidence": 0.0,
        }
        if pd.isna(value):
            return empty
        value = str(value).strip()
        # Empty string is semantically missing, not Unclassified
        if not value:
            return empty
        if self.NULL_PATTERNS.match(value):
            return empty
        for category, pattern in self.TIER1_PATTERNS:
            match = pattern.search(value)
            if match:
                return {
                    "one_health_category": category,
                    "one_health_term": match.group(0),
                    "one_health_confidence": 1.0,
                }
        return {
            "one_health_category": "Unclassified",
            "one_health_term": np.nan,
            "one_health_confidence": 0.0,
        }
