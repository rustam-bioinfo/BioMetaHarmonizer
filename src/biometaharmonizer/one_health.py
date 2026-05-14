import functools
import json
import logging
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    from rapidfuzz import process as _rfprocess, fuzz as _rfuzz
    _RAPIDFUZZ_AVAILABLE = True
except ImportError:
    _RAPIDFUZZ_AVAILABLE = False
    logger.warning(
        "rapidfuzz not installed; fuzzy fallback disabled. "
        "pip install rapidfuzz>=3.0.0"
    )

# ---------------------------------------------------------------------------
# Improvement #6 (optional NCBI Taxonomy live lookup)
# ---------------------------------------------------------------------------
try:
    import urllib.request as _urllib_request
    import urllib.parse as _urllib_parse
    _URLLIB_AVAILABLE = True
except ImportError:
    _URLLIB_AVAILABLE = False


_REQUIRED_DICT_KEYS = frozenset({
    "ontology_map",
    "host_to_category",
    "unambiguous_human_terms",
    "unambiguous_animal_terms",
    "ambiguous_specimen_terms",
    "synonym_map",
    "tier1_patterns",
})


def _load_dictionaries(path):
    """
    Load one_health_dictionaries.json from *path* (if given and exists),
    or from the bundled schemas/ location.
    """
    candidate = None
    if path is not None:
        p = Path(path)
        if p.exists():
            candidate = p
        else:
            raise FileNotFoundError(
                f"one_health_dictionaries.json not found at the supplied path: {path}. "
                "Check the path or omit it to use the bundled dictionary."
            )

    if candidate is None:
        bundled = Path(__file__).parent / "schemas" / "one_health_dictionaries.json"
        if bundled.exists():
            candidate = bundled
        else:
            raise FileNotFoundError(
                f"one_health_dictionaries.json not found at bundled path: {bundled}. "
                "Run: python scripts/build_dictionaries.py"
            )

    try:
        with open(candidate, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"one_health_dictionaries.json is malformed at {candidate}: {exc}. "
            "Re-run scripts/build_dictionaries.py to regenerate."
        ) from exc

    missing = _REQUIRED_DICT_KEYS - data.keys()
    if missing:
        raise ValueError(
            f"one_health_dictionaries.json at {candidate} is missing required keys: "
            f"{sorted(missing)}. "
            "Re-run scripts/build_dictionaries.py to regenerate."
        )

    logger.debug("Loaded one_health_dictionaries.json from %s", candidate)
    return data


_INSTITUTION_KEYWORD_RE = re.compile(
    r"\b(?:"
    r"university|universit[ae]t|universite|universidad|universidade"
    r"|institute|institut"
    r"|college|school of"
    r"|laboratory|laboratories"
    r"|center|centre"
    r"|department|dept"
    r"|hospital|clinic"
    r"|foundation|association"
    r"|corporation|corp\b|inc\b|ltd\b|llc\b"
    r"|academy|academie"
    r")\b",
    re.IGNORECASE,
)

_HOST_COMMA_ADDRESS_RE = re.compile(
    r"^(?:\S+\s+){2,}\S+,\s*[A-Z][a-zA-Z]"
)

_HOST_BRACKET_RE = re.compile(r"\s*[\[\(][^\]\)]*[\]\)]\s*")

_TAXON_TOKEN_RE = re.compile(r"^[a-zA-Z\-]+$")

_SPECIES_IN_PARENS_RE = re.compile(r"^[^(]+\(([^)]+)\)\s*$")

_ANIMAL_ORIGIN_RE = re.compile(
    r"\ba\s+(?:blood|feces|fecal|urine|tissue|swab)\s+(?:sample\s+)?of\s+(\w+)\s+origin\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Improvement #2: negation-aware preprocessing
# ---------------------------------------------------------------------------
_NEGATION_PREFIX_RE = re.compile(
    r"\b(?:not|non[\-\s]|without|excluding|absent|negative\s+for|no\b)\s+",
    re.IGNORECASE,
)

# Food-processing / food-setting signals used by Improvement #5
_FOOD_CONTEXT_RE = re.compile(
    r"\b(?:abattoir|slaughterhouse|processing\s+plant|pasteuriz|ferment|"
    r"ready[\-\s]to[\-\s]eat|ready[\-\s]to[\-\s]cook|packag|cann(?:ed|ing)|"
    r"butcher|meat\s+processing|dairy\s+processing|food\s+processing|"
    r"food\s+production|food\s+safety|food[\-\s]borne|foodborne|"
    r"retail\s+(?:meat|food)|supermarket|grocery|delicatessen)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Fix 1: Oil-environmental modifier suppression
# When working_clean contains an oil/petroleum/hydrocarbon modifier,
# any Animal tier-1 keyword that happens to co-occur is incidental
# (e.g. "bovine" in a petroleum-contaminated soil study). Strip Animal
# tier-1 matches from working_clean before voting so the Environmental
# keyword wins uncontested.
# ---------------------------------------------------------------------------
_OIL_ENV_MODIFIER_RE = re.compile(
    r"\b(?:oil|petroleum|crude|diesel|gasoline|kerosene|hydrocarbon|"
    r"tar|bitumen|asphalt|naphtha|refinery|petrochemical|"
    r"polycyclic\s+aromatic|pah|btex|benzene|toluene|xylene)"
    r"[\s\-]*(?:contaminated|impacted|polluted|spill|affected|"
    r"derived|based|degrading|degraded|weathered|amended|"
    r"rich|laden)?\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Fix 2: Healthcare surface reclassification
# Surface/object keywords that indicate the sample is from a hard surface
# or inanimate object in a clinical/healthcare environment, not from a
# human body. When the winning category is Human with confidence < 0.90
# and one of these keywords appears in any source field, demote to
# Environmental (the swab is of a surface, not a person).
# ---------------------------------------------------------------------------
_HEALTHCARE_SURFACE_RE = re.compile(
    r"\b(?:door\s*handle|door\s*knob|countertop|counter\s+top|"
    r"floor|ceiling|wall\s+surface|bench\s+top|benchtop|"
    r"sink|drain|faucet|tap|tap\s+water\s+outlet|"
    r"keyboard|computer\s+keyboard|mouse\s+device|touchscreen|"
    r"bedrail|bed\s+rail|handrail|grab\s+bar|"
    r"stethoscope|thermometer\s+surface|"
    r"swab\s+of\s+(?:a\s+)?surface|surface\s+swab|"
    r"environmental\s+swab|hospital\s+surface|"
    r"inanimate\s+surface|hard\s+surface|"
    r"medical\s+device\s+surface|equipment\s+surface)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Failure Mode C fix: lab/vaccine processing terms that suppress Animal
# when confidence is below the certainty threshold.
# Applied in _integrate_evidence after the evidence loop, before writing out.
# Threshold 0.85 ensures a rock-solid Animal signal (e.g. rumen + cell culture)
# is not wrongly suppressed.
# ---------------------------------------------------------------------------
_LAB_PROCESSING_OVERRIDE = frozenset({
    "cell culture",
    "cell line",
    "in vitro",
    "tissue culture",
    "organ culture",
    "primary culture",
    "axenic culture",
    "axenic",
    "gnotobiotic",
    "germ-free",
    "liquid culture",
    "broth culture",
    "vaccine",
    "live attenuated",
    "attenuated",
    "attenuated strain",
    "inactivated vaccine",
    "killed vaccine",
    "toxigenic strain",
    "capsulated strain",
    "laboratory medium",
    "luria bertani",
    "lb medium",
})


def _taxonomic_fallback(name_lower, host_to_category):
    """
    Progressive right-token-drop fallback for trinomial / subspecies names.

    Given a name like "equus ferus caballus" that is absent from
    host_to_category, tries progressively shorter prefixes:
      "equus ferus caballus" -> miss
      "equus ferus"          -> hit (if present) -> return category
      "equus"                -> (would try if still no hit)

    Only fires when:
      - the name has 2-4 tokens (binomial, trinomial, quadrinomial)
      - every token consists solely of letters or hyphens (no digits,
        no punctuation that would indicate a non-taxonomic string)

    Returns the category string on the first prefix hit, or None.
    """
    tokens = name_lower.split()
    if not (2 <= len(tokens) <= 4):
        return None
    if not all(_TAXON_TOKEN_RE.match(t) for t in tokens):
        return None
    for n in range(len(tokens) - 1, 0, -1):
        candidate = " ".join(tokens[:n])
        if candidate in host_to_category:
            return host_to_category[candidate]
    return None


def _is_institution_host(text):
    if _INSTITUTION_KEYWORD_RE.search(text):
        return True
    if _HOST_COMMA_ADDRESS_RE.match(text):
        return True
    return False


_HOST_PAREN_RE = re.compile(r"\s*\(.*?\)\s*")
_HOST_SUFFIX_RE = re.compile(
    r"\s+(?:"
    r"subsp\.?(?:\s+\S+)?"
    r"|ssp\.?(?:\s+\S+)?"
    r"|var\.?(?:\s+\S+)?"
    r"|f\.(?:\s+\S+)?"
    r"|sp\.?"
    r"|colony\s+\S+"
    r"|strain\s+\S+"
    r"|[A-Z][a-z]*\."
    r")$",
    re.IGNORECASE,
)


def _normalize_host_name(text):
    cleaned = _HOST_PAREN_RE.sub(" ", text).strip()
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = _HOST_SUFFIX_RE.sub("", cleaned).strip()
    return cleaned.lower()


def _tier1_to_pattern(value):
    if isinstance(value, list):
        escaped = "|".join(re.escape(t) for t in value)
        pattern = r"\b(?:" + escaped + r")\b"
    else:
        pattern = value
    return re.compile(pattern, re.IGNORECASE)


_FIELD_WEIGHTS = {
    "isolation_source":  1.00,
    "host":              1.00,
    "env_medium":        0.85,
    "env_local_scale":   0.80,
    "sample_type":       0.70,
    "env_broad_scale":   0.50,
    "setting_inference": None,
}

_CONFIDENCE_LEVELS = [
    (0.85, "high"),
    (0.60, "medium"),
    (0.30, "low"),
]

# Penalty applied when domain and specimen tracks disagree (Improvement #1).
_CONFLICT_CONFIDENCE_PENALTY = 0.15


def _term_specificity(term_str, source):
    if source in ("unambiguous", "host_dict"):
        return 1.0
    if source == "ambiguous":
        return 0.3
    if source == "none":
        return 0.0
    if source == "tier1":
        n = len(term_str) if term_str else 0
        if n >= 8:
            return 0.90
        if n >= 4:
            return 0.75
        return 0.50
    return 0.0


def discretize_confidence(score):
    """
    Convert a numeric confidence score to a human-readable evidence level.

    Returns one of: "high", "medium", "low", "unresolved".
    """
    for threshold, label in _CONFIDENCE_LEVELS:
        if score >= threshold:
            return label
    return "unresolved"


_CLASSIFY_TEXT_KEYS = frozenset({
    "one_health_category",
    "one_health_term",
    "one_health_confidence",
    "one_health_term_source",
    "one_health_processing",
    "one_health_setting",
})

# Categories that may appear as output of the classifier.
# 'Lab' is intentionally excluded: Lab signals are handled via
# processing_terms and institution_patterns, not emitted as a
# classifiable One Health category.
_VALID_CATEGORIES = frozenset({
    "Human",
    "Animal",
    "Plant",
    "Food",
    "Environmental",
    "Unclassified",
})

# ---------------------------------------------------------------------------
# Improvement #6: NCBI Taxonomy live lookup helper (module-level, cacheable)
# ---------------------------------------------------------------------------
_NCBI_TAXONOMY_CACHE: dict = {}
_NCBI_EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_NCBI_ANIMAL_CLASSES = frozenset({
    "Mammalia", "Aves", "Reptilia", "Amphibia", "Actinopterygii",
    "Chondrichthyes", "Insecta", "Arachnida", "Malacostraca",
    "Gastropoda", "Bivalvia", "Cephalopoda", "Annelida", "Nematoda",
})
_NCBI_PLANT_DIVISIONS = frozenset({
    "Viridiplantae", "Streptophyta", "Chlorophyta", "Rhodophyta",
    "Phaeophyceae",
})


def _ncbi_taxonomy_lookup(name: str) -> str | None:
    """
    Query NCBI Taxonomy E-utils for *name* and infer One Health category
    from its lineage (Animal, Plant, or None for unresolved/Human/other).

    Results are cached in _NCBI_TAXONOMY_CACHE to avoid repeated network
    calls for the same name within a process lifetime.

    Returns category string ("Animal" | "Plant") or None.
    """
    if not _URLLIB_AVAILABLE:
        return None
    cached = _NCBI_TAXONOMY_CACHE.get(name)
    if cached is not None:
        return cached if cached != "__none__" else None

    try:
        search_url = (
            f"{_NCBI_EUTILS_BASE}/esearch.fcgi?"
            + _urllib_parse.urlencode({
                "db": "taxonomy",
                "term": name,
                "retmode": "json",
                "retmax": "1",
            })
        )
        with _urllib_request.urlopen(search_url, timeout=3) as resp:
            search_data = json.loads(resp.read().decode("utf-8"))
        ids = search_data.get("esearchresult", {}).get("idlist", [])
        if not ids:
            _NCBI_TAXONOMY_CACHE[name] = "__none__"
            return None

        fetch_url = (
            f"{_NCBI_EUTILS_BASE}/efetch.fcgi?"
            + _urllib_parse.urlencode({
                "db": "taxonomy",
                "id": ids[0],
                "retmode": "json",
            })
        )
        with _urllib_request.urlopen(fetch_url, timeout=3) as resp:
            fetch_data = json.loads(resp.read().decode("utf-8"))
        taxa = fetch_data.get("result", {}).get(ids[0], {})
        lineage_ex = taxa.get("lineageex", [])
        lineage_names = {t.get("scientificname", "") for t in lineage_ex}

        category = None
        if lineage_names & _NCBI_ANIMAL_CLASSES:
            category = "Animal"
        elif lineage_names & _NCBI_PLANT_DIVISIONS:
            category = "Plant"

        _NCBI_TAXONOMY_CACHE[name] = category if category is not None else "__none__"
        return category

    except Exception as exc:
        logger.debug("NCBI taxonomy lookup failed for %r: %s", name, exc)
        _NCBI_TAXONOMY_CACHE[name] = "__none__"
        return None


class OneHealthClassifier:
    """
    Module 5: One Health Categorization.

    Classifies records into standardized One Health tiers using deterministic,
    multi-layer semantic decomposition. All biological knowledge is loaded from
    one_health_dictionaries.json.

    Improvements applied (v2):
      #1  Structured conflict detection between domain and specimen tracks
          with confidence penalty and evidence_conflict output flag.
      #2  Negation-aware preprocessing: keywords preceded by negation tokens
          are suppressed rather than matched.
      #3  Multi-term consensus voting within a single field value: all tier-1
          matches are collected and the plurality-vote category wins.
      #4  Context-assisted resolution of ambiguous terms using corroborating
          evidence from other fields before giving up.
      #5  Food-context post-classification override: Animal winner is demoted
          to Food when a food-processing or food-setting signal co-occurs.
      #6  Optional NCBI Taxonomy live fallback for host names not found in
          host_to_category (opt-in via use_ncbi_fallback=True).
      #7  Composite-string fallback: low-confidence results trigger a second
          pass over the concatenation of all non-null field values.
      #8  Per-category specificity overrides via "specificity_overrides" dict
          in the JSON, enabling curators to tune individual term scores.
      #9  Category yield priority: in mixed-signal fields Animal yields to
          Food, Environmental, Plant, and Human when any of those categories
          also match (prevents animal ingredient names in food dishes or
          environmental sample descriptions from overriding context).
      #C  Lab/vaccine processing override (Failure Mode C fix): when
          one_health_processing is a known lab or vaccine term and the
          winning Animal confidence is below 0.85, the category is demoted
          to Unclassified. This fixes cell-culture environments, vaccine
          strains, and laboratory media records misclassified as Animal.
          Exemption: direct host_dict organism-name hits are not demoted
          (tracked via _domain_from_host_dict flag).
      #F1 Oil-environmental modifier suppression: when working_clean contains
          an oil/petroleum/hydrocarbon modifier, Animal tier-1 matches are
          stripped before voting so the Environmental keyword wins.
      #F2 Healthcare surface reclassification: Human (confidence < 0.90)
          is demoted to Environmental when a hard-surface/object keyword
          appears in any source field value.
      #F3 Lab override exemption for explicit organism names: _domain_from_host_dict
          flag prevents #C from demoting records whose Animal category came
          from a direct host_to_category dict lookup (known organism name).

    MED-4: _classify_text results are memoized per instance via an LRU cache
    (maxsize=4096) to avoid redundant pattern matching when the same text value
    appears in many records (e.g. "blood", "human", "clinical").
    """

    _FIELD_PRIORITY = [
        "isolation_source",
        "host",
        "env_medium",
        "env_local_scale",
        "env_broad_scale",
        "sample_type",
    ]

    _DOMAIN_FIELDS = {"sample_type"}
    _SUPPORTING_FIELDS = {"env_broad_scale"}
    _SPECIMEN_FIELDS = {"isolation_source", "env_medium", "env_local_scale"}

    NULL_PATTERNS = re.compile(
        r"^(?:-+|\.+|n/?a|na|nd|nr|ns|nt|none|null|nil|"
        r"missing|misssing|missng|mising|"
        r"unknown|unkown|unknwon|unknow|"
        r"not\s+provided|not\s+collected|not\s+applicable|not\s+available|"
        r"not\s+determined|not\s+recorded|not\s+reported|not\s+known|"
        r"not\s+given|not\s+stated|not\s+specified|"
        r"not\s+done|not\s+tested|not\s+sequenced|not\s+typed|"
        r"unavailable|unspecified|undetermined|unidentified|"
        r"restricted|restricted\s+access|withheld|confidential|"
        r"tbd|tba|"
        r"missing\s*:.*|not\s+applicable\s*:.*|data\s+agreement\s+established\s+pre-?2023)$",
        re.IGNORECASE,
    )

    def __init__(
        self,
        dictionary_path=None,
        fuzzy_threshold=92,
        use_ncbi_fallback=False,
    ):
        self._dicts = _load_dictionaries(dictionary_path)
        self._fuzzy_threshold = fuzzy_threshold
        self._use_ncbi_fallback = use_ncbi_fallback  # Improvement #6

        self._abbrev_map = {
            k.lower(): v.lower()
            for k, v in self._dicts.get("abbreviation_map", {}).items()
        }

        self._synonym_map = sorted(
            ((k.lower(), v.lower()) for k, v in self._dicts.get("synonym_map", {}).items()),
            key=lambda x: len(x[0]),
            reverse=True,
        )
        self._synonym_patterns = [
            (re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE), canonical)
            for phrase, canonical in self._synonym_map
        ]

        self._host_to_category = {
            k.lower(): v
            for k, v in self._dicts.get("host_to_category", {}).items()
        }
        self._ambiguous_terms = {
            t.lower() for t in self._dicts.get("ambiguous_specimen_terms", [])
        }
        self._unambiguous_human = {
            t.lower() for t in self._dicts.get("unambiguous_human_terms", [])
        }
        self._unambiguous_animal = {
            t.lower() for t in self._dicts.get("unambiguous_animal_terms", [])
        }

        self._ambiguous_category_terms = {
            k.lower(): v
            for k, v in self._dicts.get("ambiguous_category_terms", {}).items()
        }
        self._ambiguous_category_set = set(self._ambiguous_category_terms.keys())

        inst_patterns = self._dicts.get("institution_patterns", [])
        coll_prefixes = self._dicts.get("culture_collection_prefixes", [])
        all_inst = inst_patterns + coll_prefixes
        if all_inst:
            self._INSTITUTION_RE = re.compile(
                "|".join(re.escape(p) for p in all_inst), re.IGNORECASE
            )
        else:
            self._INSTITUTION_RE = None

        proc_terms = self._dicts.get("processing_terms", {})
        if proc_terms:
            sorted_terms = sorted(proc_terms.keys(), key=len, reverse=True)
            self._PROCESSING_RE = re.compile(
                r"\b(" + "|".join(re.escape(k) for k in sorted_terms) + r")\b",
                re.IGNORECASE,
            )
        else:
            self._PROCESSING_RE = None
        self._proc_specimen_map = {
            k.lower(): v
            for k, v in self._dicts.get("processing_specimen_map", {}).items()
        }

        settings = self._dicts.get("setting_patterns", [])
        if settings:
            sorted_settings = sorted(settings, key=len, reverse=True)
            self._SETTING_RE = re.compile(
                r"\b(" + "|".join(re.escape(s) for s in sorted_settings) + r")\b",
                re.IGNORECASE,
            )
        else:
            self._SETTING_RE = None
        self._setting_to_category = {
            k.lower(): v
            for k, v in self._dicts.get("setting_to_category", {}).items()
        }
        self._setting_confidence = self._dicts.get("setting_confidence", {})

        tier1_raw = self._dicts.get("tier1_patterns", {})
        tier1_order = self._dicts.get("tier1_order", list(tier1_raw.keys()))
        self._TIER1_PATTERNS = []
        for category in tier1_order:
            if category not in _VALID_CATEGORIES:
                continue
            if category in tier1_raw:
                self._TIER1_PATTERNS.append(
                    (category, _tier1_to_pattern(tier1_raw[category]))
                )

        if _RAPIDFUZZ_AVAILABLE:
            ont_map = self._dicts.get("ontology_map", {})
            self._fuzzy_corpus = []
            self._fuzzy_labels = []
            for category, terms in ont_map.items():
                if category not in _VALID_CATEGORIES:
                    continue
                for term in terms:
                    term_lower = term.lower()
                    if term_lower in self._ambiguous_category_set:
                        continue
                    self._fuzzy_corpus.append(term_lower)
                    self._fuzzy_labels.append(category)
        else:
            self._fuzzy_corpus = []
            self._fuzzy_labels = []

        # Improvement #8: per-category per-term specificity overrides.
        self._specificity_overrides: dict[str, float] = {
            k.lower(): float(v)
            for k, v in self._dicts.get("specificity_overrides", {}).items()
        }

        self._classify_text = functools.lru_cache(maxsize=4096)(self._classify_text_uncached)

    # ------------------------------------------------------------------
    # Improvement #8: term specificity with override support
    # ------------------------------------------------------------------

    def _term_specificity_with_override(self, term_str: str, source: str) -> float:
        if term_str:
            override = self._specificity_overrides.get(term_str.lower())
            if override is not None:
                return override
        return _term_specificity(term_str, source)

    # ------------------------------------------------------------------
    # Legacy public API
    # ------------------------------------------------------------------

    def classify(self, series):
        """Legacy single-field classification. Returns pd.Series."""
        return series.apply(lambda v: self._classify_text(v)["one_health_category"])

    def _classify_single(self, value):
        return self._classify_text(value)["one_health_category"]

    def classify_joint(self, isolation_source_series, host_series):
        """Legacy two-field classification. Delegates to classify_multi_field."""
        if not isolation_source_series.index.equals(host_series.index):
            raise ValueError("classify_joint: series must share the same index.")
        df = self.classify_multi_field(
            isolation_source=isolation_source_series,
            host=host_series,
        )
        return df["one_health_category"]

    def classify_with_confidence(self, series):
        """
        Legacy single-field classification with confidence scores.

        Returns pd.DataFrame with columns:
          one_health_category, one_health_term, one_health_confidence
        """
        _required = ["one_health_category", "one_health_term", "one_health_confidence"]
        rows = []
        for v in series:
            result = self._classify_text(v)
            for key in _CLASSIFY_TEXT_KEYS:
                if key not in result:
                    result[key] = "Unclassified" if key == "one_health_category" else np.nan
            rows.append({k: result[k] for k in _required})
        return pd.DataFrame(rows, index=series.index)

    # ------------------------------------------------------------------
    # Extended public API
    # ------------------------------------------------------------------

    def classify_multi_field(self, **fields):
        """
        Two-pass multi-field classification.

        Accepts named pd.Series for any of:
          isolation_source, host, env_medium,
          env_local_scale, env_broad_scale, sample_type

        Returns pd.DataFrame with columns:
          one_health_category, one_health_term, one_health_confidence,
          one_health_evidence_level, one_health_processing,
          one_health_setting, one_health_source_field,
          one_health_evidence_conflict  (Improvement #1)
        """
        known = set(self._FIELD_PRIORITY)
        for k in fields:
            if k not in known:
                warnings.warn(
                    f"classify_multi_field received unknown field name {k!r}. "
                    f"Valid field names: {sorted(known)}. "
                    "The series will be ignored.",
                    UserWarning,
                    stacklevel=2,
                )

        first = next((s for s in fields.values() if s is not None), None)
        if first is None:
            raise ValueError("classify_multi_field: no valid series provided.")
        idx = first.index

        aligned = {}
        for k in self._FIELD_PRIORITY:
            s = fields.get(k)
            aligned[k] = (
                s.reindex(idx)
                if (s is not None and not s.empty)
                else pd.Series(np.nan, index=idx, dtype=object)
            )

        records = pd.DataFrame(aligned, index=idx)
        results = [
            self._integrate_evidence(row)
            for row in records.itertuples(index=False, name="Record")
        ]
        return pd.DataFrame(results, index=idx)

    # ------------------------------------------------------------------
    # Evidence integration helpers
    # ------------------------------------------------------------------

    def _lookup_species_in_parens(self, val_str):
        """
        For specimen fields (isolation_source, env_medium, env_local_scale),
        detect the pattern '<tissue> (<species name>)' and resolve the
        parenthetical content via host_to_category + _taxonomic_fallback.

        Returns (category, species_key) or (None, None).
        """
        m = _SPECIES_IN_PARENS_RE.match(val_str.strip())
        if not m:
            return None, None
        species_raw = m.group(1).strip().lower()
        cat = self._host_to_category.get(species_raw)
        if cat is None:
            cat = _taxonomic_fallback(species_raw, self._host_to_category)
        if cat is None:
            norm = _normalize_host_name(m.group(1).strip())
            cat = self._host_to_category.get(norm)
            if cat is None:
                cat = _taxonomic_fallback(norm, self._host_to_category)
            species_raw = norm if cat else species_raw
        return cat, species_raw

    def _resolve_host_category(self, val_str: str):
        """
        Full host name resolution pipeline including NCBI fallback
        (Improvement #6). Returns (category, lookup_term) or (None, val_str).
        """
        host_clean = _HOST_BRACKET_RE.sub(" ", val_str).strip()
        host_key = host_clean.lower()

        host_cat = self._host_to_category.get(host_key)
        lookup_term = host_key
        if host_cat is None:
            norm_key = _normalize_host_name(host_clean)
            host_cat = self._host_to_category.get(norm_key)
            lookup_term = norm_key if host_cat is not None else host_key

        if host_cat is None:
            host_cat = _taxonomic_fallback(host_key, self._host_to_category)
            if host_cat is None:
                host_cat = _taxonomic_fallback(
                    _normalize_host_name(host_clean), self._host_to_category
                )
            lookup_term = host_key

        # Improvement #6: NCBI live lookup as last resort
        if host_cat is None and self._use_ncbi_fallback:
            host_cat = _ncbi_taxonomy_lookup(host_clean)
            if host_cat is not None:
                logger.debug("NCBI taxonomy resolved %r -> %s", host_clean, host_cat)

        return host_cat, lookup_term

    # ------------------------------------------------------------------
    # Improvement #3 + #9: multi-term voting with category yield priority
    # ------------------------------------------------------------------

    _CATEGORY_YIELD_PRIORITY: dict[str, int] = {
        "Human":         1,
        "Food":          2,
        "Environmental": 3,
        "Plant":         4,
        "Animal":        5,
        "Unclassified":  6,
    }

    def _tier1_vote(self, working: str):
        """
        Collect ALL tier-1 keyword matches in *working* and return the
        plurality-vote (category, best_term, best_specificity) tuple, or
        (None, None, 0.0) if no match.

        Votes are weighted by per-term specificity. In mixed-signal fields
        (multiple categories matched), _CATEGORY_YIELD_PRIORITY determines
        the winner: the category with the lowest priority number wins,
        provided it has at least 0.50 aggregate specificity votes.
        """
        votes: dict[str, float] = {}
        best_per_cat: dict[str, tuple[str, float]] = {}

        for category, pattern in self._TIER1_PATTERNS:
            for m in pattern.finditer(working):
                term = m.group(0)
                spec = self._term_specificity_with_override(term, "tier1")
                votes[category] = votes.get(category, 0.0) + spec
                prev_term, prev_spec = best_per_cat.get(category, ("", 0.0))
                if spec > prev_spec or (spec == prev_spec and len(term) > len(prev_term)):
                    best_per_cat[category] = (term, spec)

        if not votes:
            return None, None, 0.0

        if len(votes) == 1:
            winner = next(iter(votes))
        else:
            priority_winner = min(
                votes,
                key=lambda c: (
                    self._CATEGORY_YIELD_PRIORITY.get(c, 99),
                    -votes[c],
                    -len(best_per_cat[c][0]),
                ),
            )
            vote_winner = max(
                votes,
                key=lambda c: (votes[c], len(best_per_cat[c][0])),
            )
            if (
                priority_winner != vote_winner
                and votes[priority_winner] >= 0.50
            ):
                winner = priority_winner
            else:
                winner = vote_winner

        best_term, best_spec = best_per_cat[winner]
        return winner, best_term, best_spec

    # ------------------------------------------------------------------
    # Improvement #4: context-assisted ambiguous term resolution
    # ------------------------------------------------------------------

    def _resolve_ambiguous_with_context(
        self,
        evidence_term,
        domain_category,
        specimen_category,
        supporting_category,
    ):
        """
        When only an ambiguous term was captured and no category was
        conclusively assigned, try to infer a category from other tracks
        (Improvement #4).
        """
        context_cat = domain_category or specimen_category or supporting_category
        if context_cat and context_cat != "Unclassified":
            return context_cat

        if evidence_term and evidence_term in self._ambiguous_category_terms:
            candidates = self._ambiguous_category_terms[evidence_term]
            if isinstance(candidates, str):
                return candidates
            if isinstance(candidates, list) and len(candidates) == 1:
                return candidates[0]

        return None

    # ------------------------------------------------------------------
    # Main evidence integration loop
    # ------------------------------------------------------------------

    def _integrate_evidence(self, row):
        out = {
            "one_health_category":          "Unclassified",
            "one_health_term":              np.nan,
            "one_health_confidence":        0.0,
            "one_health_evidence_level":    "unresolved",
            "one_health_processing":        np.nan,
            "one_health_setting":           np.nan,
            "one_health_source_field":      np.nan,
            "one_health_evidence_conflict": False,  # Improvement #1
        }

        domain_category      = None
        domain_term          = None
        domain_field         = None
        domain_specificity   = 0.0
        domain_field_weight  = 0.0

        specimen_category     = None
        specimen_term         = None
        specimen_field        = None
        specimen_specificity  = 0.0
        specimen_field_weight = 0.0

        evidence_term   = None
        evidence_field  = None

        supporting_category = None
        supporting_term     = None
        supporting_field    = None
        supporting_conf     = 0.0

        corroborated = False

        # Fix 3: track whether domain_category came from a direct host_dict
        # lookup (explicit organism name). Used to exempt the #C lab override.
        _domain_from_host_dict = False

        # Track all non-null field string values for composite pass (Improvement #7)
        field_values_for_composite: list = []

        for field in self._FIELD_PRIORITY:
            val = getattr(row, field, None)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue
            val_str = str(val).strip()
            if not val_str or self.NULL_PATTERNS.match(val_str):
                continue

            field_values_for_composite.append(val_str)

            if field == "host":
                if _is_institution_host(val_str):
                    continue

                host_cat, lookup_term = self._resolve_host_category(val_str)

                if host_cat is not None:
                    spec = self._term_specificity_with_override(lookup_term, "host_dict")
                    fw = _FIELD_WEIGHTS["host"]
                    if domain_category is None:
                        domain_category     = host_cat
                        domain_term         = lookup_term
                        domain_field        = field
                        domain_specificity  = spec
                        domain_field_weight = fw
                        _domain_from_host_dict = True  # Fix 3: explicit organism name
                    elif domain_category == host_cat:
                        corroborated = True
                    layer = self._classify_text(val_str)
                    if pd.notna(layer.get("one_health_processing")) and pd.isna(out["one_health_processing"]):
                        out["one_health_processing"] = layer["one_health_processing"]
                    if pd.notna(layer.get("one_health_setting")) and pd.isna(out["one_health_setting"]):
                        out["one_health_setting"] = layer["one_health_setting"]
                    continue

                layer = self._classify_text(val_str)
                if pd.notna(layer.get("one_health_processing")) and pd.isna(out["one_health_processing"]):
                    out["one_health_processing"] = layer["one_health_processing"]
                if pd.notna(layer.get("one_health_setting")) and pd.isna(out["one_health_setting"]):
                    out["one_health_setting"] = layer["one_health_setting"]

                cat = layer.get("one_health_category")
                if cat is None or cat not in _VALID_CATEGORIES or cat == "Unclassified":
                    continue
                term_lower = str(layer.get("one_health_term") or val_str).lower()
                if term_lower in self._ambiguous_category_set:
                    if evidence_term is None:
                        evidence_term = term_lower
                        evidence_field = field
                    continue
                tsource = layer.get("one_health_term_source", "tier1")
                spec = self._term_specificity_with_override(term_lower, tsource)
                if tsource == "fuzzy":
                    spec = layer.get("one_health_confidence", 0.0)
                fw = _FIELD_WEIGHTS["host"] * 0.90
                if domain_category is None:
                    domain_category     = cat
                    domain_term         = layer.get("one_health_term") or val_str
                    domain_field        = field
                    domain_specificity  = spec
                    domain_field_weight = fw
                elif domain_category == cat:
                    corroborated = True
                continue

            if field in self._SPECIMEN_FIELDS:
                paren_cat, paren_term = self._lookup_species_in_parens(val_str)
                if paren_cat is not None:
                    fw = _FIELD_WEIGHTS.get(field, 1.00)
                    if specimen_category is None:
                        specimen_category     = paren_cat
                        specimen_term         = paren_term
                        specimen_field        = field
                        specimen_specificity  = 0.90
                        specimen_field_weight = fw
                    elif specimen_category == paren_cat:
                        corroborated = True
                    continue

            layer = self._classify_text(val_str)

            if pd.notna(layer.get("one_health_processing")) and pd.isna(out["one_health_processing"]):
                out["one_health_processing"] = layer["one_health_processing"]
            if pd.notna(layer.get("one_health_setting")) and pd.isna(out["one_health_setting"]):
                out["one_health_setting"] = layer["one_health_setting"]

            cat = layer.get("one_health_category")

            if cat is None or cat not in _VALID_CATEGORIES or cat == "Unclassified":
                continue

            term_lower = str(layer.get("one_health_term") or val_str).lower()
            tsource = layer.get("one_health_term_source", "tier1")
            spec = self._term_specificity_with_override(term_lower, tsource)
            if tsource == "fuzzy":
                spec = layer.get("one_health_confidence", 0.0)

            fw = _FIELD_WEIGHTS.get(field, 0.70)

            if field in self._SUPPORTING_FIELDS:
                if term_lower not in self._ambiguous_category_set:
                    if supporting_category is None:
                        supporting_category = cat
                        supporting_term     = layer["one_health_term"]
                        supporting_field    = field
                        supporting_conf     = spec * fw
                    elif supporting_category == (domain_category or specimen_category):
                        corroborated = True
                continue

            if field in self._DOMAIN_FIELDS:
                if term_lower in self._ambiguous_category_set:
                    if evidence_term is None:
                        evidence_term = term_lower
                        evidence_field = field
                    continue
                if domain_category is None:
                    domain_category     = cat
                    domain_term         = layer["one_health_term"]
                    domain_field        = field
                    domain_specificity  = spec
                    domain_field_weight = fw
                elif domain_category == cat:
                    corroborated = True
                continue

            if term_lower in self._ambiguous_category_set:
                if evidence_term is None:
                    evidence_term = term_lower
                    evidence_field = field
            elif term_lower in self._ambiguous_terms:
                if specimen_term is None:
                    specimen_term         = term_lower
                    specimen_field        = field
                    specimen_specificity  = 0.3
                    specimen_field_weight = fw
            elif term_lower in self._unambiguous_human:
                if specimen_category is None:
                    specimen_category     = "Human"
                    specimen_term         = term_lower
                    specimen_field        = field
                    specimen_specificity  = self._term_specificity_with_override(term_lower, "unambiguous")
                    specimen_field_weight = fw
                elif specimen_category == "Human":
                    corroborated = True
            elif term_lower in self._unambiguous_animal:
                if specimen_category is None:
                    specimen_category     = "Animal"
                    specimen_term         = term_lower
                    specimen_field        = field
                    specimen_specificity  = self._term_specificity_with_override(term_lower, "unambiguous")
                    specimen_field_weight = fw
                elif specimen_category == "Animal":
                    corroborated = True
            else:
                if specimen_category is None:
                    specimen_category     = cat
                    specimen_term         = term_lower
                    specimen_field        = field
                    specimen_specificity  = spec
                    specimen_field_weight = fw
                elif specimen_category == cat:
                    corroborated = True

        # ------------------------------------------------------------------
        # Improvement #1: detect domain vs. specimen conflict
        # ------------------------------------------------------------------
        evidence_conflict = False
        if (
            domain_category is not None
            and specimen_category is not None
            and domain_category != specimen_category
            and domain_specificity >= 0.75
            and specimen_specificity >= 0.75
        ):
            evidence_conflict = True

        corroboration_bonus = 0.10 if corroborated else 0.0

        if domain_category is not None:
            raw_conf = min(1.0, domain_specificity * domain_field_weight + corroboration_bonus)
            if evidence_conflict:
                raw_conf = max(0.0, raw_conf - _CONFLICT_CONFIDENCE_PENALTY)
            out["one_health_category"]          = domain_category
            out["one_health_term"]              = domain_term
            out["one_health_confidence"]        = round(raw_conf, 3)
            out["one_health_source_field"]      = domain_field
            out["one_health_evidence_conflict"] = evidence_conflict

        elif specimen_category is not None:
            raw_conf = min(1.0, specimen_specificity * specimen_field_weight + corroboration_bonus)
            out["one_health_category"]     = specimen_category
            out["one_health_term"]         = specimen_term
            out["one_health_confidence"]   = round(raw_conf, 3)
            out["one_health_source_field"] = specimen_field

        elif specimen_term is not None or evidence_term is not None:
            # Improvement #4: try context-assisted resolution before Unclassified
            resolved_cat = self._resolve_ambiguous_with_context(
                evidence_term, domain_category, specimen_category, supporting_category
            )
            source = specimen_field if specimen_field is not None else evidence_field
            raw_conf = min(1.0, 0.3 * (specimen_field_weight or 0.70) + corroboration_bonus)
            if resolved_cat and resolved_cat != "Unclassified":
                out["one_health_category"]     = resolved_cat
                out["one_health_term"]         = specimen_term or evidence_term
                out["one_health_confidence"]   = round(raw_conf * 0.80, 3)
                out["one_health_source_field"] = source
            else:
                out["one_health_category"]     = "Unclassified"
                out["one_health_term"]         = specimen_term or evidence_term
                out["one_health_confidence"]   = round(raw_conf, 3)
                out["one_health_source_field"] = source

        else:
            if supporting_category is not None:
                out["one_health_category"]     = supporting_category
                out["one_health_term"]         = supporting_term
                out["one_health_confidence"]   = round(supporting_conf, 3)
                out["one_health_source_field"] = supporting_field
            else:
                setting_val = out.get("one_health_setting")
                if pd.notna(setting_val):
                    setting_lower = str(setting_val).lower()
                    inferred = self._setting_to_category.get(setting_lower)
                    if inferred:
                        raw_conf = self._setting_confidence.get(setting_lower, 0.40)
                        out["one_health_category"]     = inferred
                        out["one_health_confidence"]   = round(raw_conf, 3)
                        out["one_health_term"]         = setting_lower
                        out["one_health_source_field"] = "setting_inference"

        # ------------------------------------------------------------------
        # Improvement #7: composite-string fallback for low-confidence results
        # ------------------------------------------------------------------
        if (
            out["one_health_confidence"] < 0.80
            and out["one_health_category"] in ("Unclassified", "Environmental")
            and len(field_values_for_composite) > 1
        ):
            composite = " ".join(field_values_for_composite)
            comp_layer = self._classify_text(composite)
            comp_cat = comp_layer.get("one_health_category")
            comp_conf = comp_layer.get("one_health_confidence", 0.0)
            if (
                comp_cat
                and comp_cat not in ("Unclassified",)
                and comp_cat in _VALID_CATEGORIES
                and comp_conf > out["one_health_confidence"]
            ):
                out["one_health_category"]     = comp_cat
                out["one_health_term"]         = comp_layer.get("one_health_term", np.nan)
                out["one_health_confidence"]   = round(comp_conf * 0.90, 3)
                out["one_health_source_field"] = "composite"

        # ------------------------------------------------------------------
        # Improvement #5: Food-context post-classification override
        # ------------------------------------------------------------------
        if out["one_health_category"] == "Animal":
            food_signal = False
            setting_val = out.get("one_health_setting")
            if pd.notna(setting_val) and _FOOD_CONTEXT_RE.search(str(setting_val)):
                food_signal = True
            proc_val = out.get("one_health_processing")
            if pd.notna(proc_val) and _FOOD_CONTEXT_RE.search(str(proc_val)):
                food_signal = True
            if not food_signal:
                for fv in field_values_for_composite:
                    if _FOOD_CONTEXT_RE.search(fv):
                        food_signal = True
                        break
            if food_signal:
                food_layer = self._classify_text(
                    " ".join(field_values_for_composite)
                )
                if food_layer.get("one_health_category") == "Food":
                    out["one_health_category"]    = "Food"
                    out["one_health_term"]        = food_layer.get("one_health_term", out["one_health_term"])
                    out["one_health_confidence"]  = round(
                        min(0.85, out["one_health_confidence"]), 3
                    )
                    out["one_health_source_field"] = "food_override"

        # ------------------------------------------------------------------
        # Failure Mode C fix (#C): lab/vaccine processing override
        # When a lab or vaccine processing term was detected and the current
        # Animal winner has confidence < 0.85, demote to Unclassified.
        # The 0.85 threshold protects rock-solid Animal signals (e.g. rumen
        # content classified from isolation_source with a host dict hit).
        # Fix 3 exemption: when _domain_from_host_dict is True (the Animal
        # category was assigned from a direct host_to_category dict lookup
        # for an explicit organism name), the lab/vaccine term describes the
        # experimental context, not the sample origin, so the Animal category
        # is correct and should not be demoted.
        # ------------------------------------------------------------------
        if out["one_health_category"] == "Animal":
            proc_val = out.get("one_health_processing")
            if (
                pd.notna(proc_val)
                and str(proc_val).lower() in _LAB_PROCESSING_OVERRIDE
                and out["one_health_confidence"] < 0.85
                and not _domain_from_host_dict  # Fix 3: exempt explicit organism names
            ):
                out["one_health_category"]       = "Unclassified"
                out["one_health_confidence"]     = round(out["one_health_confidence"] * 0.5, 4)
                out["one_health_evidence_level"] = "unresolved"

        # ------------------------------------------------------------------
        # Fix 2: Healthcare surface reclassification
        # A record classified as Human whose source text contains a surface or
        # inanimate-object keyword is more accurately Environmental: the Human
        # signal came from the clinical setting, not from the sample matrix.
        # Only fires when confidence < 0.90 to protect strong direct human
        # specimen signals (blood, CSF, biopsy, etc.).
        # ------------------------------------------------------------------
        if out["one_health_category"] == "Human" and out["one_health_confidence"] < 0.90:
            for _fv in field_values_for_composite:
                if _HEALTHCARE_SURFACE_RE.search(_fv):
                    out["one_health_category"]   = "Environmental"
                    out["one_health_confidence"] = round(out["one_health_confidence"] * 0.85, 3)
                    break

        out["one_health_evidence_level"] = discretize_confidence(out["one_health_confidence"])
        return out

    # ------------------------------------------------------------------
    # Text preprocessing helpers
    # ------------------------------------------------------------------

    def _expand_abbreviations(self, text):
        tokens = re.split(r"([\s/\-]+)", text)
        expanded = []
        for tok in tokens:
            if re.fullmatch(r"[\s/\-]+", tok):
                expanded.append(tok)
                continue
            tok_clean = tok.lower().rstrip(".,;:")
            expanded.append(self._abbrev_map.get(tok_clean, tok))
        return "".join(expanded)

    def _normalize_synonyms(self, text):
        result = text
        for pattern, canonical in self._synonym_patterns:
            result = pattern.sub(canonical, result)
        return result

    # ------------------------------------------------------------------
    # Improvement #2: negation-aware keyword suppression
    # ------------------------------------------------------------------

    def _suppress_negated_spans(self, text: str) -> str:
        """
        Remove tokens that are immediately preceded by a negation prefix
        (not, non-, without, excluding, absent, negative for).
        The negation prefix itself is also removed so it does not accidentally
        match shorter tier-1 patterns on partial text.
        """
        result = _NEGATION_PREFIX_RE.sub(" __NEG__ ", text)
        tokens = result.split()
        cleaned = []
        negate_next = False
        for tok in tokens:
            if tok == "__NEG__":
                negate_next = True
                continue
            if negate_next:
                negate_next = False
                continue
            cleaned.append(tok)
        return " ".join(cleaned)

    # ------------------------------------------------------------------
    # Core single-value classification engine (wrapped by LRU cache in __init__)
    # ------------------------------------------------------------------

    def _classify_text_uncached(self, value):
        """
        Classify a single text value. Called via self._classify_text which is
        an LRU-cached wrapper created in __init__ (MED-4).

        Returns dict with keys:
          one_health_category, one_health_term, one_health_confidence,
          one_health_term_source, one_health_processing, one_health_setting
        """
        unclassified = {
            "one_health_category":    "Unclassified",
            "one_health_term":        np.nan,
            "one_health_confidence":  0.0,
            "one_health_term_source": "none",
            "one_health_processing":  np.nan,
            "one_health_setting":     np.nan,
        }

        if value is None or (isinstance(value, float) and np.isnan(value)):
            return unclassified

        text = str(value).strip()
        if not text or self.NULL_PATTERNS.match(text):
            return unclassified

        text = text.replace("_", " ")

        if self._INSTITUTION_RE and self._INSTITUTION_RE.search(text):
            text = self._INSTITUTION_RE.sub("", text).strip(" .,;:-")
            if not text:
                return unclassified

        text = _INSTITUTION_KEYWORD_RE.sub("", text).strip()
        if not text:
            return unclassified

        origin_m = _ANIMAL_ORIGIN_RE.search(text)
        if origin_m:
            animal_noun = origin_m.group(1).lower()
            cat = self._host_to_category.get(animal_noun)
            if cat is None:
                cat = _taxonomic_fallback(animal_noun, self._host_to_category)
            if cat is not None:
                return {
                    "one_health_category":    cat,
                    "one_health_term":        animal_noun,
                    "one_health_confidence":  0.90,
                    "one_health_term_source": "host_dict",
                    "one_health_processing":  np.nan,
                    "one_health_setting":     np.nan,
                }

        text = self._expand_abbreviations(text)
        working = self._normalize_synonyms(text)

        processing = np.nan
        setting = np.nan

        if self._PROCESSING_RE:
            pmatch = self._PROCESSING_RE.search(working)
            if pmatch:
                matched_proc = pmatch.group(1).lower()
                processing = matched_proc
                specimen_override = self._proc_specimen_map.get(matched_proc)
                if specimen_override:
                    working = (
                        working[: pmatch.start()].strip()
                        + " " + specimen_override + " "
                        + working[pmatch.end():].strip()
                    ).strip()
                else:
                    working = (working[: pmatch.start()] + working[pmatch.end():]).strip()

        if self._SETTING_RE:
            smatch = self._SETTING_RE.search(working)
            if smatch:
                setting = smatch.group(1).lower()
                working = (working[: smatch.start()] + working[smatch.end():]).strip()

        # Improvement #2: suppress negated keyword spans before tier-1 matching
        working_clean = self._suppress_negated_spans(working)

        # Fix 1: oil-environmental modifier suppression
        # When the working string contains an oil/petroleum/hydrocarbon modifier,
        # strip all Animal tier-1 matches so that the Environmental keyword
        # wins uncontested. Animal-looking tokens in such strings are incidental
        # (e.g. an animal common name embedded in a petroleum-environment study).
        if _OIL_ENV_MODIFIER_RE.search(working_clean):
            for _animal_cat, _animal_pat in self._TIER1_PATTERNS:
                if _animal_cat == "Animal":
                    working_clean = _animal_pat.sub("", working_clean).strip()
                    break

        if working_clean:
            # Improvements #3 + #9: collect all tier-1 matches and vote with yield priority
            winner_cat, winner_term, winner_spec = self._tier1_vote(working_clean)
            if winner_cat is not None:
                return {
                    "one_health_category":    winner_cat,
                    "one_health_term":        winner_term,
                    "one_health_confidence":  winner_spec,
                    "one_health_term_source": "tier1",
                    "one_health_processing":  processing,
                    "one_health_setting":     setting,
                }

        if _RAPIDFUZZ_AVAILABLE and self._fuzzy_corpus and working_clean and len(working_clean) > 2:
            result = _rfprocess.extractOne(
                working_clean.lower(),
                self._fuzzy_corpus,
                scorer=_rfuzz.WRatio,
                score_cutoff=self._fuzzy_threshold,
            )
            if result:
                best_term, score, best_idx = result
                fuzzy_conf = round(score / 100.0, 3)
                return {
                    "one_health_category":    self._fuzzy_labels[best_idx],
                    "one_health_term":        best_term,
                    "one_health_confidence":  fuzzy_conf,
                    "one_health_term_source": "fuzzy",
                    "one_health_processing":  processing,
                    "one_health_setting":     setting,
                }

        unclassified["one_health_processing"] = processing
        unclassified["one_health_setting"] = setting
        return unclassified
