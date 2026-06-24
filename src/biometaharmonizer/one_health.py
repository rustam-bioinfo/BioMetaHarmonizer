import functools
import json
import logging
import math
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
# Failure Mode C fix: lab/vaccine processing terms
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

# ---------------------------------------------------------------------------
# Columns produced internally by _integrate_evidence that are not
# part of the public output schema.
# ---------------------------------------------------------------------------
_INTERNAL_COLUMNS = frozenset({
    "one_health_evidence_conflict",
    "one_health_evidence_sources",
    "one_health_term",
    "one_health_processing",
    "one_health_setting",
    "one_health_source_field",
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
    "env_broad_scale":   0.70,
    "sample_type":       0.10,
    "setting_inference": None,
}

_CONFIDENCE_LEVELS = [
    (0.85, "high"),
    (0.60, "medium"),
    (0.30, "low"),
]

# Penalty applied when domain and specimen tracks disagree (Improvement #1).
_CONFLICT_CONFIDENCE_PENALTY = 0.15

# ---------------------------------------------------------------------------
# Improvement #10: Bayesian fusion constants
# ---------------------------------------------------------------------------
# Minimum posterior probability for the Bayesian winner to override the
# deterministic result. Below this threshold the deterministic path wins.
_BAYES_MIN_POSTERIOR = 0.55

# Minimum field weight for a hard-evidence source (host_dict or unambiguous
# term with spec=1.0) to be treated as deterministic and bypass Bayesian
# fusion entirely for that record.
_BAYES_HARD_EVIDENCE_FW_THRESHOLD = 0.80

# Epsilon to prevent division by zero in LR calculation.
_BAYES_EPS = 1e-9

# Ordered list of the five classifiable categories (Unclassified excluded
# from the posterior because it is a default, not a positive signal).
_CLASSIFIABLE_CATS = ["Human", "Animal", "Plant", "Food", "Environmental"]


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
          also match.
      #10 Bayesian multi-field evidence fusion: each BioSample field is
          treated as an independent noisy sensor. Evidence tuples
          (category, specificity, field_weight) collected across all fields
          are converted to per-category log-odds updates using the likelihood
          ratio LR = (spec * fw) / (1 - spec * fw + eps). Softmax over
          accumulated log-odds yields a posterior distribution; argmax is
          the Bayesian winner. The Bayesian result overrides the
          deterministic result only when:
            (a) no hard evidence exists (no host_dict hit and no
                unambiguous-term match with spec=1.0 from a field with
                weight >= 0.80), AND
            (b) the Bayesian winner posterior >= _BAYES_MIN_POSTERIOR
                (default 0.55), AND
            (c) the Bayesian winner is not 'Unclassified'.
          When hard evidence is present, the deterministic path wins and
          the Bayesian posterior is computed but used only for confidence
          score refinement.
          New output column: one_health_evidence_sources (int) -- count
          of fields that contributed non-ambiguous evidence.
      #C  Lab/vaccine processing override (Failure Mode C fix).
      #F1 Oil-environmental modifier suppression.
      #F2 Healthcare surface reclassification.
      #F3 Lab override exemption for explicit organism names.
      #IS (v2) Three-step precedence for _SPECIMEN_FIELDS:
          Step 1 — _classify_text first; accept Human/Food/Environmental/
          Plant immediately (strong ontology coverage).
          Step 2 — verbatim host_to_category lookup only when Step 1
          returns Unclassified or Animal; recovers bare scientific
          binomials / common names absent from tier1_patterns.
          Step 3 — fall through, reusing pre_layer (no redundant call).

    MED-4: _classify_text results are memoized per instance via an LRU cache
    (maxsize=4096) to avoid redundant pattern matching when the same text value
    appears in many records.
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
        self._use_ncbi_fallback = use_ncbi_fallback

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
        Multi-field classification with Bayesian evidence fusion (#10).

        Accepts named pd.Series for any of:
          isolation_source, host, env_medium,
          env_local_scale, env_broad_scale, sample_type

        Returns pd.DataFrame with columns:
          one_health_category, one_health_confidence,
          one_health_evidence_level
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
        out_df = pd.DataFrame(results, index=idx)
        return out_df.drop(columns=[c for c in _INTERNAL_COLUMNS if c in out_df.columns])

    # ------------------------------------------------------------------
    # Evidence integration helpers
    # ------------------------------------------------------------------

    def _lookup_species_in_parens(self, val_str):
        """
        Detect '<tissue> (<species name>)' and resolve the parenthetical
        content via host_to_category + _taxonomic_fallback.
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
        Full host name resolution pipeline including NCBI fallback (#6).
        Returns (category, lookup_term) or (None, val_str).
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
        When only an ambiguous term was captured, try to infer a category
        from corroborating tracks before giving up (#4).
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
    # Improvement #10: Bayesian evidence combination
    # ------------------------------------------------------------------

    @staticmethod
    def _bayesian_combine(
        evidence_tuples: list,
    ) -> dict[str, float]:
        """
        Convert a list of (category, specificity, field_weight) evidence
        tuples into a posterior probability distribution over the five
        classifiable categories using naive-Bayes log-odds accumulation.

        Model:
          - Uniform prior over the five categories.
          - For each evidence tuple the likelihood ratio is:
              LR(cat) = p / (1 - p + eps)
            where p = specificity * field_weight, clamped to (0, 1).
          - The log-LR is added to log_odds[cat] for the matching
            category. All other categories receive a small symmetric
            penalty (-log_lr * 0.25) to express that evidence for one
            category is mild counter-evidence against the others.
          - Softmax over log_odds yields the posterior.

        Returns dict {category: posterior_probability} for the five
        classifiable categories. Sum of values is 1.0.
        Returns empty dict when evidence_tuples is empty.
        """
        if not evidence_tuples:
            return {}

        log_odds = {c: 0.0 for c in _CLASSIFIABLE_CATS}

        for cat, spec, fw in evidence_tuples:
            if cat not in log_odds:
                continue
            p = min(0.9999, max(_BAYES_EPS, spec * fw))
            log_lr = math.log(p / (1.0 - p + _BAYES_EPS))
            log_odds[cat] += log_lr
            penalty = abs(log_lr) * 0.25
            for other in _CLASSIFIABLE_CATS:
                if other != cat:
                    log_odds[other] -= penalty

        max_lo = max(log_odds.values())
        exp_vals = {c: math.exp(v - max_lo) for c, v in log_odds.items()}
        total = sum(exp_vals.values())
        return {c: round(v / total, 4) for c, v in exp_vals.items()}

    # ------------------------------------------------------------------
    # Main evidence collection loop (feeds both deterministic and Bayes)
    # ------------------------------------------------------------------

    def _collect_field_evidence(self, row, out):
        """
        Iterate over all BioSample fields for *row*, run the per-field
        classification logic, and return:

          deterministic_state  -- dict with the same keys as _integrate_evidence
                                  uses internally (domain_*, specimen_*,
                                  supporting_*, corroborated, evidence_*,
                                  _domain_from_host_dict).
          evidence_tuples      -- list of (category, specificity, field_weight)
                                  for non-ambiguous field hits; used by
                                  _bayesian_combine.
          field_values         -- list of raw non-null field value strings;
                                  used by composite fallback (#7) and food
                                  override (#5).

        Side effects: populates out["one_health_processing"] and
        out["one_health_setting"] from the first field that provides them.
        """
        state = {
            "domain_category":      None,
            "domain_term":          None,
            "domain_field":         None,
            "domain_specificity":   0.0,
            "domain_field_weight":  0.0,
            "specimen_category":    None,
            "specimen_term":        None,
            "specimen_field":       None,
            "specimen_specificity": 0.0,
            "specimen_field_weight": 0.0,
            "evidence_term":        None,
            "evidence_field":       None,
            "supporting_category":  None,
            "supporting_term":      None,
            "supporting_field":     None,
            "supporting_conf":      0.0,
            "corroborated":         False,
            "_domain_from_host_dict": False,
            # hard-evidence flag for Bayesian gating: True when a
            # host_dict or unambiguous-spec=1.0 hit from a high-weight
            # field has been seen.
            "_hard_evidence": False,
        }
        evidence_tuples = []
        field_values = []

        for field in self._FIELD_PRIORITY:
            val = getattr(row, field, None)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue
            val_str = str(val).strip()
            if not val_str or self.NULL_PATTERNS.match(val_str):
                continue

            field_values.append(val_str)
            fw = _FIELD_WEIGHTS.get(field, 0.70)

            if field == "host":
                if _is_institution_host(val_str):
                    continue

                host_cat, lookup_term = self._resolve_host_category(val_str)

                if host_cat is not None:
                    spec = self._term_specificity_with_override(lookup_term, "host_dict")
                    layer = self._classify_text(val_str)
                    if pd.notna(layer.get("one_health_processing")) and pd.isna(out["one_health_processing"]):
                        out["one_health_processing"] = layer["one_health_processing"]
                    if pd.notna(layer.get("one_health_setting")) and pd.isna(out["one_health_setting"]):
                        out["one_health_setting"] = layer["one_health_setting"]
                    if state["domain_category"] is None:
                        state["domain_category"]     = host_cat
                        state["domain_term"]         = lookup_term
                        state["domain_field"]        = field
                        state["domain_specificity"]  = spec
                        state["domain_field_weight"] = fw
                        state["_domain_from_host_dict"] = True
                        if fw >= _BAYES_HARD_EVIDENCE_FW_THRESHOLD:
                            state["_hard_evidence"] = True
                    elif state["domain_category"] == host_cat:
                        state["corroborated"] = True
                    evidence_tuples.append((host_cat, spec, fw))
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
                    if state["evidence_term"] is None:
                        state["evidence_term"] = term_lower
                        state["evidence_field"] = field
                    continue
                tsource = layer.get("one_health_term_source", "tier1")
                spec = self._term_specificity_with_override(term_lower, tsource)
                if tsource == "fuzzy":
                    spec = layer.get("one_health_confidence", 0.0)
                fw_host_text = fw * 0.90
                if state["domain_category"] is None:
                    state["domain_category"]     = cat
                    state["domain_term"]         = layer.get("one_health_term") or val_str
                    state["domain_field"]        = field
                    state["domain_specificity"]  = spec
                    state["domain_field_weight"] = fw_host_text
                elif state["domain_category"] == cat:
                    state["corroborated"] = True
                evidence_tuples.append((cat, spec, fw_host_text))
                continue

            if field in self._SPECIMEN_FIELDS:
                paren_cat, paren_term = self._lookup_species_in_parens(val_str)
                if paren_cat is not None:
                    if state["specimen_category"] is None:
                        state["specimen_category"]     = paren_cat
                        state["specimen_term"]         = paren_term
                        state["specimen_field"]        = field
                        state["specimen_specificity"]  = 0.90
                        state["specimen_field_weight"] = fw
                    elif state["specimen_category"] == paren_cat:
                        state["corroborated"] = True
                    evidence_tuples.append((paren_cat, 0.90, fw))
                    continue

                # Fix #IS (v2) — three-step precedence for _SPECIMEN_FIELDS:
                #
                # Step 1: Run _classify_text first (ontology_map / tier1 /
                #         fuzzy). If it returns Human, Food, Environmental, or
                #         Plant accept the result immediately. These categories
                #         have strong ontology coverage and must not be
                #         overwritten by a later verbatim host-dict probe
                #         (e.g. "milk" or "soil" would otherwise be shadowed
                #         if a token happened to match a host entry).
                #
                # Step 2: Only if Step 1 misses (Unclassified or Animal), do
                #         a verbatim whole-string lookup in host_to_category.
                #         This recovers bare scientific binomials / common
                #         names (e.g. "Sus scrofa", "bos taurus") absent from
                #         tier1_patterns. Animal is included here because
                #         host_to_category is the authoritative organism->
                #         category map and is often more precise than tier1.
                #
                # Step 3: Fall through — reuse pre_layer result in the
                #         shared classify_text handling block below.

                pre_layer = self._classify_text(val_str)

                if pd.notna(pre_layer.get("one_health_processing")) and pd.isna(out["one_health_processing"]):
                    out["one_health_processing"] = pre_layer["one_health_processing"]
                if pd.notna(pre_layer.get("one_health_setting")) and pd.isna(out["one_health_setting"]):
                    out["one_health_setting"] = pre_layer["one_health_setting"]

                pre_cat = pre_layer.get("one_health_category")

                # Step 1: strong ontology hit for Human / Food / Environmental / Plant
                if pre_cat in {"Human", "Food", "Environmental", "Plant"}:
                    term_lower = str(pre_layer.get("one_health_term") or val_str).lower()
                    tsource = pre_layer.get("one_health_term_source", "tier1")
                    spec = self._term_specificity_with_override(term_lower, tsource)
                    if tsource == "fuzzy":
                        spec = pre_layer.get("one_health_confidence", 0.0)
                    if state["specimen_category"] is None:
                        state["specimen_category"]     = pre_cat
                        state["specimen_term"]         = term_lower
                        state["specimen_field"]        = field
                        state["specimen_specificity"]  = spec
                        state["specimen_field_weight"] = fw
                        if spec >= 1.0 and fw >= _BAYES_HARD_EVIDENCE_FW_THRESHOLD:
                            state["_hard_evidence"] = True
                    elif state["specimen_category"] == pre_cat:
                        state["corroborated"] = True
                    evidence_tuples.append((pre_cat, spec, fw))
                    continue

                # Step 2: verbatim whole-string host_to_category lookup
                host_cat, lookup_term = self._resolve_host_category(val_str)
                if host_cat is not None:
                    spec = self._term_specificity_with_override(lookup_term, "host_dict")
                    if state["specimen_category"] is None:
                        state["specimen_category"]     = host_cat
                        state["specimen_term"]         = lookup_term
                        state["specimen_field"]        = field
                        state["specimen_specificity"]  = spec
                        state["specimen_field_weight"] = fw
                        if fw >= _BAYES_HARD_EVIDENCE_FW_THRESHOLD:
                            state["_hard_evidence"] = True
                    elif state["specimen_category"] == host_cat:
                        state["corroborated"] = True
                    evidence_tuples.append((host_cat, spec, fw))
                    continue

                # Step 3: fall through — reuse pre_layer result in the
                # shared classify_text handling block below.
                layer = pre_layer
            else:
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

            if field in self._SUPPORTING_FIELDS:
                if term_lower not in self._ambiguous_category_set:
                    if state["supporting_category"] is None:
                        state["supporting_category"] = cat
                        state["supporting_term"]     = layer["one_health_term"]
                        state["supporting_field"]    = field
                        state["supporting_conf"]     = spec * fw
                    elif state["supporting_category"] == (state["domain_category"] or state["specimen_category"]):
                        state["corroborated"] = True
                    evidence_tuples.append((cat, spec, fw))
                continue

            if field in self._DOMAIN_FIELDS:
                if term_lower in self._ambiguous_category_set:
                    if state["evidence_term"] is None:
                        state["evidence_term"] = term_lower
                        state["evidence_field"] = field
                    continue
                if state["domain_category"] is None:
                    state["domain_category"]     = cat
                    state["domain_term"]         = layer["one_health_term"]
                    state["domain_field"]        = field
                    state["domain_specificity"]  = spec
                    state["domain_field_weight"] = fw
                elif state["domain_category"] == cat:
                    state["corroborated"] = True
                evidence_tuples.append((cat, spec, fw))
                continue

            if term_lower in self._ambiguous_category_set:
                if state["evidence_term"] is None:
                    state["evidence_term"] = term_lower
                    state["evidence_field"] = field
            elif term_lower in self._ambiguous_terms:
                if state["specimen_term"] is None:
                    state["specimen_term"]         = term_lower
                    state["specimen_field"]        = field
                    state["specimen_specificity"]  = 0.3
                    state["specimen_field_weight"] = fw
            elif term_lower in self._unambiguous_human:
                _spec_u = self._term_specificity_with_override(term_lower, "unambiguous")
                if state["specimen_category"] is None:
                    state["specimen_category"]     = "Human"
                    state["specimen_term"]         = term_lower
                    state["specimen_field"]        = field
                    state["specimen_specificity"]  = _spec_u
                    state["specimen_field_weight"] = fw
                    if fw >= _BAYES_HARD_EVIDENCE_FW_THRESHOLD:
                        state["_hard_evidence"] = True
                elif state["specimen_category"] == "Human":
                    state["corroborated"] = True
                evidence_tuples.append(("Human", _spec_u, fw))
            elif term_lower in self._unambiguous_animal:
                _spec_u = self._term_specificity_with_override(term_lower, "unambiguous")
                if state["specimen_category"] is None:
                    state["specimen_category"]     = "Animal"
                    state["specimen_term"]         = term_lower
                    state["specimen_field"]        = field
                    state["specimen_specificity"]  = _spec_u
                    state["specimen_field_weight"] = fw
                    if fw >= _BAYES_HARD_EVIDENCE_FW_THRESHOLD:
                        state["_hard_evidence"] = True
                elif state["specimen_category"] == "Animal":
                    state["corroborated"] = True
                evidence_tuples.append(("Animal", _spec_u, fw))
            else:
                if state["specimen_category"] is None:
                    state["specimen_category"]     = cat
                    state["specimen_term"]         = term_lower
                    state["specimen_field"]        = field
                    state["specimen_specificity"]  = spec
                    state["specimen_field_weight"] = fw
                elif state["specimen_category"] == cat:
                    state["corroborated"] = True
                evidence_tuples.append((cat, spec, fw))

        return state, evidence_tuples, field_values

    # ------------------------------------------------------------------
    # Main evidence integration (orchestrates deterministic + Bayesian)
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
            "one_health_evidence_conflict": False,
            "one_health_evidence_sources":  0,
        }

        state, evidence_tuples, field_values_for_composite = self._collect_field_evidence(row, out)

        domain_category      = state["domain_category"]
        domain_term          = state["domain_term"]
        domain_field         = state["domain_field"]
        domain_specificity   = state["domain_specificity"]
        domain_field_weight  = state["domain_field_weight"]
        specimen_category    = state["specimen_category"]
        specimen_term        = state["specimen_term"]
        specimen_field       = state["specimen_field"]
        specimen_specificity = state["specimen_specificity"]
        specimen_field_weight = state["specimen_field_weight"]
        evidence_term        = state["evidence_term"]
        evidence_field       = state["evidence_field"]
        supporting_category  = state["supporting_category"]
        supporting_term      = state["supporting_term"]
        supporting_field     = state["supporting_field"]
        supporting_conf      = state["supporting_conf"]
        corroborated         = state["corroborated"]
        _domain_from_host_dict = state["_domain_from_host_dict"]
        _hard_evidence         = state["_hard_evidence"]

        out["one_health_evidence_sources"] = len(evidence_tuples)

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

        # ------------------------------------------------------------------
        # Improvement #10: Bayesian posterior
        # ------------------------------------------------------------------
        posteriors = self._bayesian_combine(evidence_tuples)
        bayes_winner = None
        bayes_posterior = 0.0
        if posteriors:
            bayes_winner = max(posteriors, key=lambda c: posteriors[c])
            bayes_posterior = posteriors[bayes_winner]

        # ------------------------------------------------------------------
        # Deterministic path (always computed; used when hard evidence exists
        # or when Bayesian signal is too weak)
        # ------------------------------------------------------------------
        determ_category  = None
        determ_term      = None
        determ_conf      = 0.0
        determ_field     = None

        if domain_category is not None:
            raw_conf = min(1.0, domain_specificity * domain_field_weight + corroboration_bonus)
            if evidence_conflict:
                raw_conf = max(0.0, raw_conf - _CONFLICT_CONFIDENCE_PENALTY)
            determ_category = domain_category
            determ_term     = domain_term
            determ_conf     = round(raw_conf, 3)
            determ_field    = domain_field
            out["one_health_evidence_conflict"] = evidence_conflict

        elif specimen_category is not None:
            raw_conf = min(1.0, specimen_specificity * specimen_field_weight + corroboration_bonus)
            determ_category = specimen_category
            determ_term     = specimen_term
            determ_conf     = round(raw_conf, 3)
            determ_field    = specimen_field

        elif specimen_term is not None or evidence_term is not None:
            resolved_cat = self._resolve_ambiguous_with_context(
                evidence_term, domain_category, specimen_category, supporting_category
            )
            source = specimen_field if specimen_field is not None else evidence_field
            raw_conf = min(1.0, 0.3 * (specimen_field_weight or 0.70) + corroboration_bonus)
            if resolved_cat and resolved_cat != "Unclassified":
                determ_category = resolved_cat
                determ_term     = specimen_term or evidence_term
                determ_conf     = round(raw_conf * 0.80, 3)
                determ_field    = source
            else:
                determ_category = "Unclassified"
                determ_term     = specimen_term or evidence_term
                determ_conf     = round(raw_conf, 3)
                determ_field    = source

        elif supporting_category is not None:
            determ_category = supporting_category
            determ_term     = supporting_term
            determ_conf     = round(supporting_conf, 3)
            determ_field    = supporting_field

        else:
            setting_val = out.get("one_health_setting")
            if pd.notna(setting_val):
                setting_lower = str(setting_val).lower()
                inferred = self._setting_to_category.get(setting_lower)
                if inferred:
                    raw_conf = self._setting_confidence.get(setting_lower, 0.40)
                    determ_category = inferred
                    determ_conf     = round(raw_conf, 3)
                    determ_term     = setting_lower
                    determ_field    = "setting_inference"

        # ------------------------------------------------------------------
        # Decision: Bayesian override vs deterministic
        #
        # Use Bayesian result when ALL of:
        #   1. No hard evidence (host_dict or unambiguous from high-weight field)
        #   2. Bayesian winner exists and is not Unclassified
        #   3. Bayesian posterior >= _BAYES_MIN_POSTERIOR
        #   4. Either deterministic result is absent / Unclassified, OR
        #      Bayesian winner differs from deterministic AND
        #      determ_conf < 0.75 (Bayesian only overrides weak determ results)
        # ------------------------------------------------------------------
        use_bayes = (
            not _hard_evidence
            and bayes_winner is not None
            and bayes_winner != "Unclassified"
            and bayes_posterior >= _BAYES_MIN_POSTERIOR
            and (
                determ_category is None
                or determ_category == "Unclassified"
                or (bayes_winner != determ_category and determ_conf < 0.75)
            )
        )

        if use_bayes:
            out["one_health_category"]     = bayes_winner
            out["one_health_confidence"]   = round(bayes_posterior, 3)
            out["one_health_source_field"] = "bayesian_fusion"
            # Use the best-specificity term from evidence_tuples for this category
            best_ev = max(
                (ev for ev in evidence_tuples if ev[0] == bayes_winner),
                key=lambda ev: ev[1],
                default=None,
            )
            if best_ev is not None:
                # Find the term from the deterministic state that matches
                if determ_category == bayes_winner and determ_term is not None:
                    out["one_health_term"] = determ_term
                elif specimen_category == bayes_winner and specimen_term is not None:
                    out["one_health_term"] = specimen_term
                elif domain_category == bayes_winner and domain_term is not None:
                    out["one_health_term"] = domain_term
                else:
                    out["one_health_term"] = np.nan
            logger.debug(
                "Bayesian fusion overrode deterministic %r (conf=%.3f) -> %r (posterior=%.3f)",
                determ_category, determ_conf, bayes_winner, bayes_posterior,
            )
        else:
            if determ_category is not None:
                out["one_health_category"]     = determ_category
                out["one_health_term"]         = determ_term
                out["one_health_confidence"]   = determ_conf
                out["one_health_source_field"] = determ_field
            # When hard evidence AND Bayesian agree, use Bayesian posterior
            # as confidence if it is higher (benefits from multi-field corroboration)
            if (
                _hard_evidence
                and bayes_winner == out["one_health_category"]
                and bayes_posterior > out["one_health_confidence"]
            ):
                out["one_health_confidence"] = round(bayes_posterior, 3)

        # ------------------------------------------------------------------
        # Improvement #7: composite-string fallback for low-confidence results
        # ------------------------------------------------------------------
        if (
            out["one_health_confidence"] < 0.80
            # and out["one_health_category"] in ("Unclassified", "Environmental")
            and out["one_health_category"] == "Unclassified"
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
                out["one_health_term"]         = comp_layer.get("one_health_term")
                out["one_health_confidence"]   = round(comp_conf * 0.85, 3)
                out["one_health_source_field"] = "composite_fallback"

        # ------------------------------------------------------------------
        # Improvement #5: Food-context post-classification override
        # ------------------------------------------------------------------
        if out["one_health_category"] == "Animal":
            all_vals = " ".join(field_values_for_composite)
            if _FOOD_CONTEXT_RE.search(all_vals):
                out["one_health_category"]   = "Food"
                out["one_health_confidence"] = round(
                    min(1.0, out["one_health_confidence"] * 0.90), 3
                )

        # ------------------------------------------------------------------
        # Fix #F1: Oil-environmental modifier suppression
        # ------------------------------------------------------------------
        if out["one_health_category"] == "Environmental":
            all_vals = " ".join(field_values_for_composite)
            if _OIL_ENV_MODIFIER_RE.search(all_vals):
                pass  # Environmental is correct for oil-contaminated samples

        # ------------------------------------------------------------------
        # Fix #F2: Healthcare surface reclassification
        # ------------------------------------------------------------------
        if out["one_health_category"] in ("Unclassified", "Environmental"):
            all_vals = " ".join(field_values_for_composite)
            if _HEALTHCARE_SURFACE_RE.search(all_vals):
                out["one_health_category"]   = "Environmental"
                out["one_health_confidence"] = max(
                    out["one_health_confidence"], 0.55
                )
                out["one_health_source_field"] = "healthcare_surface_re"

        # ------------------------------------------------------------------
        # Failure Mode C: lab/vaccine processing override
        # ------------------------------------------------------------------
        all_vals_lower = " ".join(field_values_for_composite).lower()
        lab_hit = next(
            (t for t in _LAB_PROCESSING_OVERRIDE if t in all_vals_lower),
            None,
        )
        if lab_hit:
            # Fix #F3: do NOT override if an explicit organism name was also
            # captured (host_dict or unambiguous match). Organism + lab
            # context is a valid real sample (e.g. vaccine production strain).
            organism_present = (
                state.get("_domain_from_host_dict")
                or (
                    state.get("specimen_category") is not None
                    and state.get("specimen_specificity", 0.0) >= 1.0
                )
            )
            # Fix C2: do NOT override when a strong specimen field
            # (isolation_source, env_medium — weight >= 0.80) already
            # resolved to a real classifiable category. A lab/processing
            # flag from a weak field (sample_type, weight < 0.80) must
            # not suppress a high-confidence Environmental/Human/etc. hit.
            strong_specimen_resolved = (
                specimen_category is not None
                and specimen_category not in ("Unclassified", "")
                and specimen_field_weight >= _BAYES_HARD_EVIDENCE_FW_THRESHOLD
            )
            if not organism_present and not strong_specimen_resolved:
                out["one_health_category"]     = "Unclassified"
                out["one_health_term"]         = lab_hit
                out["one_health_confidence"]   = 0.0
                out["one_health_source_field"] = "lab_processing_override"

        # ------------------------------------------------------------------
        # Finalise evidence level
        # ------------------------------------------------------------------
        out["one_health_evidence_level"] = discretize_confidence(
            out["one_health_confidence"]
        )

        return out

    # ------------------------------------------------------------------
    # Single-value text classifier (memoized via LRU cache)
    # ------------------------------------------------------------------

    def _classify_text_uncached(self, value):
        """
        Single-value text classification pipeline.

        Returns a dict with keys matching _CLASSIFY_TEXT_KEYS.
        This method is wrapped by an LRU cache in __init__ (MED-4).
        """
        result = {
            "one_health_category":  "Unclassified",
            "one_health_term":      np.nan,
            "one_health_confidence": 0.0,
            "one_health_term_source": "none",
            "one_health_processing": np.nan,
            "one_health_setting":   np.nan,
        }

        if value is None or (isinstance(value, float) and np.isnan(value)):
            return result

        raw = str(value).strip()
        if not raw or self.NULL_PATTERNS.match(raw):
            return result

        # ------------------------------------------------------------------
        # Text normalization
        # ------------------------------------------------------------------
        
        # Step 1: Lowercase
        working = raw.lower()
        
        # Step 2: Unicode → ASCII
        import unicodedata
        working = unicodedata.normalize("NFKD", working)
        working = working.encode("ascii", "ignore").decode("ascii")
        
        # Step 3: Underscore → space
        working = working.replace("_", " ")
        
        # Step 4: Non-breaking space and typographic dashes → space
        working = (
            working
            .replace("\u00a0", " ")   # non-breaking space
            .replace("\u2013", " ")   # en-dash
            .replace("\u2014", " ")   # em-dash
        )
        
        # Step 5: Slash and backslash → space
        working = working.replace("/", " ").replace("\\", " ")
        
        # Step 6: Strip wrapping punctuation characters
        working = re.sub(r"[\"'`\[\](){}]", " ", working)
        
        # Step 7: Strip leading/trailing junk punctuation
        working = working.strip(":;*.,!?")
        
        # Step 8: Remove digits
        working = re.sub(r"\d+", " ", working)
        
        # Step 9: Collapse whitespace
        working = re.sub(r"\s+", " ", working).strip()

        # ------------------------------------------------------------------
        # Abbreviation expansion
        # ------------------------------------------------------------------
        expanded = self._abbrev_map.get(working)
        if expanded:
            working = expanded

        # ------------------------------------------------------------------
        # Synonym normalisation
        # ------------------------------------------------------------------
        for pattern, canonical in self._synonym_patterns:
            working = pattern.sub(canonical, working)

        # ------------------------------------------------------------------
        # Negation suppression
        # ------------------------------------------------------------------
        working = _NEGATION_PREFIX_RE.sub("__NEGATED__ ", working)

        # ------------------------------------------------------------------
        # Processing term detection
        # ------------------------------------------------------------------
        if self._PROCESSING_RE:
            pm = self._PROCESSING_RE.search(working)
            if pm:
                proc_key = pm.group(1).lower()
                result["one_health_processing"] = proc_key
                specimen_override = self._proc_specimen_map.get(proc_key)
                if specimen_override:
                    result["one_health_category"]   = specimen_override
                    result["one_health_term"]       = proc_key
                    result["one_health_confidence"] = 0.70
                    result["one_health_term_source"] = "processing"
                    return result

        # ------------------------------------------------------------------
        # Setting detection
        # ------------------------------------------------------------------
        if self._SETTING_RE:
            sm = self._SETTING_RE.search(working)
            if sm:
                setting_key = sm.group(1).lower()
                result["one_health_setting"] = setting_key

        # ------------------------------------------------------------------
        # Institution / culture-collection detection
        # ------------------------------------------------------------------
        if self._INSTITUTION_RE and self._INSTITUTION_RE.search(working):
            return result
        if _is_institution_host(working):
            return result

        # ------------------------------------------------------------------
        # Unambiguous human terms
        # ------------------------------------------------------------------
        for term in self._unambiguous_human:
            if re.search(r"\b" + re.escape(term) + r"\b", working):
                spec = self._term_specificity_with_override(term, "unambiguous")
                result.update({
                    "one_health_category":   "Human",
                    "one_health_term":       term,
                    "one_health_confidence": round(spec, 3),
                    "one_health_term_source": "unambiguous",
                })
                return result

        # ------------------------------------------------------------------
        # Unambiguous animal terms
        # ------------------------------------------------------------------
        for term in self._unambiguous_animal:
            if re.search(r"\b" + re.escape(term) + r"\b", working):
                spec = self._term_specificity_with_override(term, "unambiguous")
                result.update({
                    "one_health_category":   "Animal",
                    "one_health_term":       term,
                    "one_health_confidence": round(spec, 3),
                    "one_health_term_source": "unambiguous",
                })
                return result

        # ------------------------------------------------------------------
        # Tier-1 pattern voting (#3 + #9)
        # ------------------------------------------------------------------
        t1_cat, t1_term, t1_spec = self._tier1_vote(working)
        if t1_cat and t1_cat != "Unclassified":
            result.update({
                "one_health_category":   t1_cat,
                "one_health_term":       t1_term,
                "one_health_confidence": round(t1_spec, 3),
                "one_health_term_source": "tier1",
            })
            return result

        # ------------------------------------------------------------------
        # Ambiguous category-term check
        # ------------------------------------------------------------------
        for term in self._ambiguous_category_set:
            if re.search(r"\b" + re.escape(term) + r"\b", working):
                result.update({
                    "one_health_term":       term,
                    "one_health_term_source": "ambiguous",
                })
                return result

        # ------------------------------------------------------------------
        # Fuzzy matching fallback (rapidfuzz)
        # ------------------------------------------------------------------
        if _RAPIDFUZZ_AVAILABLE and self._fuzzy_corpus:
            match_result = _rfprocess.extractOne(
                working,
                self._fuzzy_corpus,
                scorer=_rfuzz.token_sort_ratio,
                score_cutoff=self._fuzzy_threshold,
            )
            if match_result:
                matched_term, score, idx = match_result
                fuzzy_cat = self._fuzzy_labels[idx]
                fuzzy_conf = round(score / 100.0, 3)
                result.update({
                    "one_health_category":   fuzzy_cat,
                    "one_health_term":       matched_term,
                    "one_health_confidence": fuzzy_conf,
                    "one_health_term_source": "fuzzy",
                })
                return result

        return result
