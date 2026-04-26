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


def _load_dictionaries(path):
    if path is not None and Path(path).exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    bundled = Path(__file__).parent / "schemas" / "one_health_dictionaries.json"
    if bundled.exists():
        with open(bundled, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# ---------------------------------------------------------------------------
# Institution / organisation guard for the host field
# ---------------------------------------------------------------------------
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


def _is_institution_host(text):
    if _INSTITUTION_KEYWORD_RE.search(text):
        return True
    if _HOST_COMMA_ADDRESS_RE.match(text):
        return True
    return False


# ---------------------------------------------------------------------------
# Host name normalisation
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Confidence helpers
# ---------------------------------------------------------------------------

#: Field weights reflect how reliably each NCBI field encodes the
#: biological origin of the isolate.
_FIELD_WEIGHTS = {
    "isolation_source":  1.00,
    "host":              1.00,   # dict hit; text hit uses 0.90 multiplier (see code)
    "env_medium":        0.85,
    "env_local_scale":   0.80,
    "sample_type":       0.70,
    "env_broad_scale":   0.50,
    "setting_inference": None,   # uses setting_confidence dict value directly
}

#: Discretization thresholds (inclusive lower bound).
_CONFIDENCE_LEVELS = [
    (0.85, "high"),
    (0.60, "medium"),
    (0.30, "low"),
]


def _term_specificity(term_str, source):
    """
    Return a specificity score in [0, 1] for a matched term.

    source values:
      "unambiguous"  -> 1.0  (from unambiguous_human/animal lists)
      "host_dict"    -> 1.0  (explicit host_to_category lookup)
      "tier1"        -> length-graded: >=8 chars 0.90, 4-7 chars 0.75, <4 chars 0.50
      "fuzzy"        -> caller passes WRatio / 100 directly
      "ambiguous"    -> 0.3
      "none"         -> 0.0
    """
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

    Thresholds:
      >= 0.85  -> "high"
      >= 0.60  -> "medium"
      >= 0.30  -> "low"
      <  0.30  -> "unresolved"

    Returns one of: "high", "medium", "low", "unresolved".
    """
    for threshold, label in _CONFIDENCE_LEVELS:
        if score >= threshold:
            return label
    return "unresolved"


# ---------------------------------------------------------------------------
# Expected output schema
# ---------------------------------------------------------------------------
_CLASSIFY_TEXT_KEYS = frozenset({
    "one_health_category",
    "one_health_term",
    "one_health_confidence",
    "one_health_term_source",
    "one_health_processing",
    "one_health_setting",
})

_VALID_CATEGORIES = frozenset({
    "Human",
    "Animal",
    "Aquatic",
    "Wildlife",
    "Plant",
    "Food",
    "Environmental",
    "Lab",
    "Unclassified",
})


class OneHealthClassifier:
    """
    Module 5: One Health Categorization.

    Classifies records into standardized One Health tiers using
    deterministic, multi-layer semantic decomposition across six
    metadata fields without any hardcoded biological terms.

    All biological knowledge (patterns, terms, categories, rules) is
    loaded exclusively from one_health_dictionaries.json.

    Confidence model
    ----------------
    one_health_confidence is a float in [0, 1] computed as:

        confidence = min(1.0, term_specificity * field_weight + corroboration)

    where:
      term_specificity  reflects how specific the matched term is:
                          unambiguous list / host dict hit -> 1.0
                          tier1 phrase >= 8 chars          -> 0.90
                          tier1 term 4-7 chars             -> 0.75
                          tier1 term < 4 chars             -> 0.50
                          fuzzy match                      -> WRatio / 100
                          ambiguous term                   -> 0.30
      field_weight      reflects how reliably the source field encodes
                        biological origin:
                          isolation_source / host dict     -> 1.00
                          host text hit                    -> 0.90
                          env_medium                       -> 0.85
                          env_local_scale                  -> 0.80
                          sample_type                      -> 0.70
                          env_broad_scale                  -> 0.50
      corroboration     +0.10 bonus when a second independent field
                        agrees with the same category, capped at 1.0

    one_health_evidence_level discretizes the score:
      >= 0.85  -> "high"
      >= 0.60  -> "medium"
      >= 0.30  -> "low"
      <  0.30  -> "unresolved"

    Public API
    ----------
    classify(series)                 -> pd.Series  (legacy)
    classify_joint(iso, host)        -> pd.Series  (legacy)
    classify_with_confidence(series) -> pd.DataFrame 3 cols (legacy)
    classify_multi_field(**fields)   -> pd.DataFrame 7 cols (extended)
    discretize_confidence(score)     -> str  (module-level helper)
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
        r"^(missing|unknown|n/?a|not provided|not collected|not applicable|na|none|--)$",
        re.IGNORECASE,
    )

    def __init__(self, dictionary_path=None, fuzzy_threshold=92):
        self._dicts = _load_dictionaries(dictionary_path)
        self._fuzzy_threshold = fuzzy_threshold

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
            if category in tier1_raw:
                self._TIER1_PATTERNS.append(
                    (category, _tier1_to_pattern(tier1_raw[category]))
                )

        if _RAPIDFUZZ_AVAILABLE:
            ont_map = self._dicts.get("ontology_map", {})
            self._fuzzy_corpus = []
            self._fuzzy_labels = []
            for category, terms in ont_map.items():
                for term in terms:
                    term_lower = term.lower()
                    if term_lower in self._ambiguous_category_set:
                        continue
                    self._fuzzy_corpus.append(term_lower)
                    self._fuzzy_labels.append(category)
        else:
            self._fuzzy_corpus = []
            self._fuzzy_labels = []

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
          one_health_setting, one_health_source_field

        one_health_category is always a string, never NaN.
        one_health_evidence_level is one of: high, medium, low, unresolved.
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
    # Two-pass evidence integration
    # ------------------------------------------------------------------

    def _integrate_evidence(self, row):
        out = {
            "one_health_category":       "Unclassified",
            "one_health_term":           np.nan,
            "one_health_confidence":     0.0,
            "one_health_evidence_level": "unresolved",
            "one_health_processing":     np.nan,
            "one_health_setting":        np.nan,
            "one_health_source_field":   np.nan,
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

        for field in self._FIELD_PRIORITY:
            val = getattr(row, field, None)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue
            val_str = str(val).strip()
            if not val_str or self.NULL_PATTERNS.match(val_str):
                continue

            # ----------------------------------------------------------
            # host field: institution guard then host_to_category lookup
            # ----------------------------------------------------------
            if field == "host":
                if _is_institution_host(val_str):
                    continue

                host_key = val_str.lower()
                host_cat = self._host_to_category.get(host_key)
                if host_cat is None:
                    norm_key = _normalize_host_name(val_str)
                    host_cat = self._host_to_category.get(norm_key)
                    lookup_term = norm_key
                else:
                    lookup_term = host_key

                if host_cat is not None:
                    spec = _term_specificity(lookup_term, "host_dict")
                    fw = _FIELD_WEIGHTS["host"]
                    if domain_category is None:
                        domain_category     = host_cat
                        domain_term         = lookup_term
                        domain_field        = field
                        domain_specificity  = spec
                        domain_field_weight = fw
                    elif domain_category == host_cat:
                        corroborated = True
                    layer = self._classify_text(val_str)
                    if pd.notna(layer.get("one_health_processing")) and pd.isna(out["one_health_processing"]):
                        out["one_health_processing"] = layer["one_health_processing"]
                    if pd.notna(layer.get("one_health_setting")) and pd.isna(out["one_health_setting"]):
                        out["one_health_setting"] = layer["one_health_setting"]
                    continue

                # No dict hit: fall through to _classify_text
                layer = self._classify_text(val_str)
                if pd.notna(layer.get("one_health_processing")) and pd.isna(out["one_health_processing"]):
                    out["one_health_processing"] = layer["one_health_processing"]
                if pd.notna(layer.get("one_health_setting")) and pd.isna(out["one_health_setting"]):
                    out["one_health_setting"] = layer["one_health_setting"]

                cat = layer.get("one_health_category")
                if cat is None or cat == "Unclassified":
                    continue
                term_lower = str(layer.get("one_health_term") or val_str).lower()
                if term_lower in self._ambiguous_category_set:
                    if evidence_term is None:
                        evidence_term = term_lower
                        evidence_field = field
                    continue
                tsource = layer.get("one_health_term_source", "tier1")
                spec = _term_specificity(term_lower, tsource)
                if tsource == "fuzzy":
                    spec = layer.get("one_health_confidence", 0.0)
                fw = _FIELD_WEIGHTS["host"] * 0.90  # text hit, slightly lower than dict
                if domain_category is None:
                    domain_category     = cat
                    domain_term         = layer.get("one_health_term") or val_str
                    domain_field        = field
                    domain_specificity  = spec
                    domain_field_weight = fw
                elif domain_category == cat:
                    corroborated = True
                continue

            # ----------------------------------------------------------
            # All other fields
            # ----------------------------------------------------------
            layer = self._classify_text(val_str)

            if pd.notna(layer.get("one_health_processing")) and pd.isna(out["one_health_processing"]):
                out["one_health_processing"] = layer["one_health_processing"]
            if pd.notna(layer.get("one_health_setting")) and pd.isna(out["one_health_setting"]):
                out["one_health_setting"] = layer["one_health_setting"]

            cat = layer.get("one_health_category")
            if cat is None or cat == "Unclassified":
                continue

            term_lower = str(layer.get("one_health_term") or val_str).lower()
            tsource = layer.get("one_health_term_source", "tier1")
            spec = _term_specificity(term_lower, tsource)
            if tsource == "fuzzy":
                spec = layer.get("one_health_confidence", 0.0)

            fw = _FIELD_WEIGHTS.get(field, 0.70)

            # env_broad_scale: supporting signal only
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

            # sample_type: domain field
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

            # specimen fields (isolation_source, env_medium, env_local_scale)
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
                    specimen_specificity  = _term_specificity(term_lower, "unambiguous")
                    specimen_field_weight = fw
                elif specimen_category == "Human":
                    corroborated = True
            elif term_lower in self._unambiguous_animal:
                if specimen_category is None:
                    specimen_category     = "Animal"
                    specimen_term         = term_lower
                    specimen_field        = field
                    specimen_specificity  = _term_specificity(term_lower, "unambiguous")
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
        # Pass 2: resolve category and compute confidence
        # ------------------------------------------------------------------
        corroboration_bonus = 0.10 if corroborated else 0.0

        if domain_category is not None:
            raw_conf = min(1.0, domain_specificity * domain_field_weight + corroboration_bonus)
            out["one_health_category"]     = domain_category
            out["one_health_term"]         = domain_term
            out["one_health_confidence"]   = round(raw_conf, 3)
            out["one_health_source_field"] = domain_field

        elif specimen_category is not None:
            raw_conf = min(1.0, specimen_specificity * specimen_field_weight + corroboration_bonus)
            out["one_health_category"]     = specimen_category
            out["one_health_term"]         = specimen_term
            out["one_health_confidence"]   = round(raw_conf, 3)
            out["one_health_source_field"] = specimen_field

        elif specimen_term is not None or evidence_term is not None:
            source = specimen_field if specimen_field is not None else evidence_field
            raw_conf = min(1.0, 0.3 * (specimen_field_weight or 0.70) + corroboration_bonus)
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
    # Core single-value classification engine
    # ------------------------------------------------------------------

    def _classify_text(self, value):
        """
        Classify a single text value.

        Returns dict with keys:
          one_health_category, one_health_term, one_health_confidence,
          one_health_term_source, one_health_processing, one_health_setting

        one_health_term_source indicates match mechanism:
          "unambiguous"  from unambiguous_human/animal lists
          "host_dict"    from host_to_category lookup
          "tier1"        from tier1_patterns regex
          "fuzzy"        from rapidfuzz fallback
          "institution"  from institution/culture collection guard
          "none"         no match
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

        if _is_institution_host(text):
            stripped = _INSTITUTION_KEYWORD_RE.sub("", text).strip(" .,;:-")
            if len(stripped) < 4:
                return {
                    "one_health_category":    "Lab",
                    "one_health_term":        text[:60],
                    "one_health_confidence":  0.9,
                    "one_health_term_source": "institution",
                    "one_health_processing":  np.nan,
                    "one_health_setting":     np.nan,
                }
            text = stripped if stripped else text

        if self._INSTITUTION_RE and self._INSTITUTION_RE.search(text):
            stripped = self._INSTITUTION_RE.sub("", text).strip(" .,;:-")
            if len(stripped) < 4:
                return {
                    "one_health_category":    "Lab",
                    "one_health_term":        text[:60],
                    "one_health_confidence":  0.9,
                    "one_health_term_source": "institution",
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

        if working:
            for category, pattern in self._TIER1_PATTERNS:
                m = pattern.search(working)
                if m:
                    matched_term = m.group(0)
                    spec = _term_specificity(matched_term, "tier1")
                    return {
                        "one_health_category":    category,
                        "one_health_term":        matched_term,
                        "one_health_confidence":  spec,
                        "one_health_term_source": "tier1",
                        "one_health_processing":  processing,
                        "one_health_setting":     setting,
                    }

        if _RAPIDFUZZ_AVAILABLE and self._fuzzy_corpus and working and len(working) > 2:
            result = _rfprocess.extractOne(
                working.lower(),
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
