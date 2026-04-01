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

    Word boundaries are applied to all short tokens to prevent
    substring false positives (e.g. 'rat' inside 'laboratory').

    NULL_PATTERNS are checked first and return NaN, not Unclassified.
    """

    NULL_PATTERNS = re.compile(
        r"^(missing|unknown|n/?a|not provided|not collected|not applicable|na|none|--)$",
        re.IGNORECASE
    )

    TIER1_PATTERNS = {
        "Human": re.compile(
            r"human|patient|clinical|homo sapiens|person|"
            r"blood|urine|sputum|wound|stool|feces|fecal|"
            r"dental|plaque|biopsy|serum|plasma|\bcsf\b|cerebrospinal|"
            r"nasopharyngeal|throat|swab|abscess|hospital",
            re.IGNORECASE
        ),
        "Animal": re.compile(
            r"bovine|cattle|\bpig\b|swine|poultry|chicken|sheep|horse|"
            r"\bdog\b|\bcat\b|rodent|\bmouse\b|\brat\b|\bbird\b|\bfish\b|animal|"
            r"insect|\bbee\b|\bant\b|wasp|queen|\bworker\b|colony|\blarva\b|larvae|"
            r"wild.caught|reared|invertebrate|arthropod|carcass|gut|flea|tick|"
            r"\bfly\b|\bmite\b|\bworm\b|nematode",
            re.IGNORECASE
        ),
        "Food": re.compile(
            r"food|\bmeat\b|\bmilk\b|cheese|vegetable|fruit|poultry product|"
            r"dairy|\begg\b|seafood|water supply|spice|grain|cereal|"
            r"flour|bread|fermented|beverage|pasta|sausage|ice.?cream|"
            r"noodle|rice|soy|tofu|sprout|produce|feed|additive|"
            r"supplement|\bcore\b|\bleaf\b|\broot\b|\bseed\b|\bhusk\b|"
            r"\bbean\b|\bnut\b|\bherb\b|spore.?forming|\bbaby\b.*wipe",
            re.IGNORECASE
        ),
        "Environmental": re.compile(
            r"soil|\bwater\b|river|lake|sewage|wastewater|\bair\b|"
            r"rhizosphere|sediment|environment|dust|biofilm|"
            r"compost|manure|surface|outdoor|indoor|cave|sand|"
            r"farm|field|forest|bark|\bmoss\b|lichen|peat|"
            r"wipe|swab.*surface|\bbaby\b.*wipe",
            re.IGNORECASE
        ),
        "Lab": re.compile(
            r"\blab\b|laboratory|\bculture\b|atcc|reference strain|"
            r"type strain|synthetic|in vitro|\bdna\b|whole organism|"
            r"genomic|sequencing|\bstrain\b.*collection",
            re.IGNORECASE
        )
    }

    def classify(self, series):
        return series.apply(self._classify_single)

    def _classify_single(self, value):
        if pd.isna(value):
            return np.nan
        value = str(value).strip()
        if self.NULL_PATTERNS.match(value):
            return np.nan
        for category, pattern in self.TIER1_PATTERNS.items():
            if pattern.search(value):
                return category
        return "Unclassified"
