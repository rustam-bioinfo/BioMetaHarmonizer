import json
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    from rapidfuzz import process as _rfprocess, fuzz as _rfuzz
    _RAPIDFUZZ_AVAILABLE = True
except ImportError:
    _RAPIDFUZZ_AVAILABLE = False
    logger.warning("rapidfuzz not installed; fuzzy fallback disabled. pip install rapidfuzz>=3.0.0")


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
# Strings like "Amity Institute of Biotechnology, Rajasthan" are deposited
# in the BioSample host attribute by submitters who confused it with an
# affiliation field.  They must never produce a biological category.
#
# Two complementary signals are checked:
#   1. _INSTITUTION_KEYWORD_RE  - well-known institutional words present
#      anywhere in the string.
#   2. _HOST_INSTITUTION_HEURISTIC_RE - structural pattern: a comma-separated
#      string of 3+ capitalised tokens that looks like an address or
#      organisation name rather than a binomial species name.
#      A binomial has exactly two tokens, no comma, first capitalised.
#      An institution name typically has >= 3 tokens and/or a comma.
#
# Both checks are applied only to the host field (see _integrate_evidence).
# The isolation_source and env_* fields are not affected.

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
    r"|biotechnology|microbiology|virology|medicine|sciences"
    r")\b",
    re.IGNORECASE,
)

# Structural heuristic: string contains a comma AND at least one token
# after the comma starts with an uppercase letter (city / state / country
# component of an address).  Biological host names do not have commas.
_HOST_COMMA_ADDRESS_RE = re.compile(
    r".+,\s*[A-Z][a-zA-Z]"
)


def _is_institution_host(text):
    """
    Return True if the host field value looks like an institution or
    address string rather than a biological organism name.

    Criteria (either is sufficient):
      - Contains an institution keyword (university, institute, college, ...)
      - Structural pattern: has a comma followed by a capitalised token,
        which is the typical form of "Organisation, City" or
        "Organisation, State" deposited by mistake in the host field.
    """
    if _INSTITUTION_KEYWORD_RE.search(text):
        return True
    if _HOST_COMMA_ADDRESS_RE.match(text):
        return True
    return False


class OneHealthClassifier:
    """
    Module 5: One Health Categorization.

    Classifies records into standardized One Health tiers using
    deterministic, multi-layer semantic decomposition across six
    metadata fields without any hardcoded biological terms.

    All biological knowledge (patterns, terms, categories, rules) is
    loaded exclusively from one_health_dictionaries.json. The code
    contains only algorithmic logic.

    Layers (applied in order per text value):
      1. Null / empty detection
      2. Institution / culture collection guard
      3. Abbreviation expansion
      4. Synonym normalization
      5. Processing-term extraction
      6. Setting-term extraction
      7. Tier-1 regex patterns (loaded from JSON, compiled at init)
      8. rapidfuzz fuzzy fallback (ontology_map corpus)

    Two-pass evidence integration in classify_multi_field():
      Pass 1 - collect signals from all fields without committing:
               domain signals  <- host / env_broad_scale / sample_type
               specimen signals <- isolation_source / env_medium / env_local_scale
               setting / processing <- all fields
      Pass 2 - resolve using term sets loaded from JSON:
               domain_category wins over specimen-only match
               ambiguous terms checked against ambiguous_category_terms for
               context-aware tiebreaking; without domain -> Unclassified

    Field priority:
      isolation_source > host > env_medium > env_local_scale > env_broad_scale > sample_type

    one_health_category is ALWAYS a string. It is never NaN.
    Values: Human | Animal | Plant | Food | Environmental | Lab | Unclassified

    Public API
    ----------
    classify(series)                 -> pd.Series  (legacy)
    classify_joint(iso, host)        -> pd.Series  (legacy, delegates to classify_multi_field)
    classify_with_confidence(series) -> pd.DataFrame 3 cols (legacy)
    classify_multi_field(**fields)   -> pd.DataFrame 6 cols (extended)
    """

    _FIELD_PRIORITY = [
        "isolation_source",
        "host",
        "env_medium",
        "env_local_scale",
        "env_broad_scale",
        "sample_type",
    ]

    _DOMAIN_FIELDS = {"host", "env_broad_scale", "sample_type"}
    _SPECIMEN_FIELDS = {"isolation_source", "env_medium", "env_local_scale"}

    NULL_PATTERNS = re.compile(
        r"^(missing|unknown|n/?a|not provided|not collected|not applicable|na|none|--)$",
        re.IGNORECASE,
    )

    def __init__(self, dictionary_path=None, fuzzy_threshold=88):
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

        # ambiguous_category_terms: term -> [list of conflicting categories]
        # produced by _resolve_collisions() in build_dictionaries.py
        self._ambiguous_category_terms = {
            k.lower(): v
            for k, v in self._dicts.get("ambiguous_category_terms", {}).items()
        }
        # flat set for fast membership check
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
                    (category, re.compile(tier1_raw[category], re.IGNORECASE))
                )

        if _RAPIDFUZZ_AVAILABLE:
            ont_map = self._dicts.get("ontology_map", {})
            self._fuzzy_corpus = []
            self._fuzzy_labels = []
            for category, terms in ont_map.items():
                for term in terms:
                    term_lower = term.lower()
                    # exclude terms that are cross-category ambiguous
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
            raise ValueError(
                "classify_joint: series must share the same index."
            )
        df = self.classify_multi_field(
            isolation_source=isolation_source_series,
            host=host_series,
        )
        return df["one_health_category"]

    def classify_with_confidence(self, series):
        rows = series.apply(self._classify_text).tolist()
        df = pd.DataFrame(rows, index=series.index)
        return df[["one_health_category", "one_health_term", "one_health_confidence"]]

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
          one_health_processing, one_health_setting, one_health_source_field

        one_health_category is always a string, never NaN.
        """
        first = next((s for s in fields.values() if s is not None), None)
        if first is None:
            raise ValueError("classify_multi_field: no valid series provided.")
        idx = first.index

        aligned = {}
        for k in self._FIELD_PRIORITY:
            s = fields.get(k)
            aligned[k] = s.reindex(idx) if (s is not None and not s.empty) else pd.Series(np.nan, index=idx, dtype=object)

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
            "one_health_category": "Unclassified",
            "one_health_term": np.nan,
            "one_health_confidence": 0.0,
            "one_health_processing": np.nan,
            "one_health_setting": np.nan,
            "one_health_source_field": np.nan,
        }

        domain_category = None
        domain_term = None
        domain_field = None
        specimen_category = None
        specimen_term = None
        specimen_field = None
        specimen_confidence = 0.0
        evidence_term = None

        for field in self._FIELD_PRIORITY:
            val = getattr(row, field, None)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue
            val_str = str(val).strip()
            if not val_str or self.NULL_PATTERNS.match(val_str):
                continue

            # Institution / address guard: applies to the host field only.
            # A value like "Amity Institute of Biotechnology, Rajasthan"
            # should never produce a biological category.  Skip the entire
            # value without further processing.
            if field == "host" and _is_institution_host(val_str):
                logger.debug(
                    "host field looks like an institution/address, skipping: %r",
                    val_str[:80],
                )
                continue

            layer = self._classify_text(val_str)

            if pd.notna(layer.get("one_health_processing")) and pd.isna(out["one_health_processing"]):
                out["one_health_processing"] = layer["one_health_processing"]

            if pd.notna(layer.get("one_health_setting")) and pd.isna(out["one_health_setting"]):
                out["one_health_setting"] = layer["one_health_setting"]

            cat = layer.get("one_health_category")
            if cat is None or cat == "Unclassified":
                continue

            term_lower = str(layer.get("one_health_term") or val_str).lower()

            if field == "host":
                host_cat = self._host_to_category.get(val_str.lower())
                if host_cat:
                    if domain_category is None:
                        domain_category = host_cat
                        domain_term = val_str
                        domain_field = field
                    continue
                if term_lower in self._ambiguous_category_set:
                    if evidence_term is None:
                        evidence_term = term_lower
                    continue
                if domain_category is None:
                    domain_category = cat
                    domain_term = layer["one_health_term"]
                    domain_field = field
                continue

            if field in self._DOMAIN_FIELDS:
                if term_lower in self._ambiguous_category_set:
                    if evidence_term is None:
                        evidence_term = term_lower
                    continue
                if domain_category is None:
                    domain_category = cat
                    domain_term = layer["one_health_term"]
                    domain_field = field
                continue

            # specimen field
            if term_lower in self._ambiguous_category_set:
                if evidence_term is None:
                    evidence_term = term_lower
                    specimen_field = field
                    specimen_confidence = 0.3
            elif term_lower in self._ambiguous_terms:
                if specimen_term is None:
                    specimen_term = term_lower
                    specimen_field = field
                    specimen_confidence = 0.3
            elif term_lower in self._unambiguous_human:
                if specimen_category is None:
                    specimen_category = "Human"
                    specimen_term = term_lower
                    specimen_field = field
                    specimen_confidence = layer["one_health_confidence"]
            elif term_lower in self._unambiguous_animal:
                if specimen_category is None:
                    specimen_category = "Animal"
                    specimen_term = term_lower
                    specimen_field = field
                    specimen_confidence = layer["one_health_confidence"]
            else:
                if specimen_category is None:
                    specimen_category = cat
                    specimen_term = term_lower
                    specimen_field = field
                    specimen_confidence = layer["one_health_confidence"]

        # ------------------------------------------------------------------
        # Pass 2: resolve
        # ------------------------------------------------------------------
        if domain_category is not None:
            supporting = specimen_term or evidence_term
            out["one_health_category"]    = domain_category
            out["one_health_term"]        = domain_term
            out["one_health_confidence"]  = 1.0 if supporting else 0.8
            out["one_health_source_field"] = domain_field
        elif specimen_category is not None:
            out["one_health_category"]    = specimen_category
            out["one_health_term"]        = specimen_term
            out["one_health_confidence"]  = specimen_confidence
            out["one_health_source_field"] = specimen_field
        elif specimen_term is not None or evidence_term is not None:
            out["one_health_category"]    = "Unclassified"
            out["one_health_term"]        = specimen_term or evidence_term
            out["one_health_confidence"]  = specimen_confidence
            out["one_health_source_field"] = specimen_field
        else:
            setting_val = out.get("one_health_setting")
            if pd.notna(setting_val):
                setting_lower = str(setting_val).lower()
                inferred = self._setting_to_category.get(setting_lower)
                if inferred:
                    out["one_health_category"]    = inferred
                    out["one_health_confidence"]  = self._setting_confidence.get(inferred, 0.4)
                    out["one_health_term"]        = setting_lower
                    out["one_health_source_field"] = "setting_inference"

        return out

    # ------------------------------------------------------------------
    # Text preprocessing helpers
    # ------------------------------------------------------------------

    def _expand_abbreviations(self, text):
        words = text.split()
        expanded = []
        for w in words:
            wl = w.lower().rstrip(".,;:")
            expanded.append(self._abbrev_map.get(wl, w))
        return " ".join(expanded)

    def _normalize_synonyms(self, text):
        text_lower = text.lower()
        for phrase, canonical in self._synonym_map:
            if phrase in text_lower:
                text_lower = text_lower.replace(phrase, canonical)
        return text_lower

    # ------------------------------------------------------------------
    # Core single-value classification engine
    # ------------------------------------------------------------------

    def _classify_text(self, value):
        unclassified = {
            "one_health_category": "Unclassified",
            "one_health_term": np.nan,
            "one_health_confidence": 0.0,
            "one_health_processing": np.nan,
            "one_health_setting": np.nan,
        }

        if value is None or (isinstance(value, float) and np.isnan(value)):
            return unclassified

        text = str(value).strip()
        if not text or self.NULL_PATTERNS.match(text):
            return unclassified

        # Layer 2: institution / culture collection guard
        if self._INSTITUTION_RE and self._INSTITUTION_RE.search(text):
            stripped = self._INSTITUTION_RE.sub("", text).strip(" .,;:-")
            if len(stripped) < 4:
                return {
                    "one_health_category": "Lab",
                    "one_health_term": text[:60],
                    "one_health_confidence": 0.9,
                    "one_health_processing": np.nan,
                    "one_health_setting": np.nan,
                }

        # Layer 3: abbreviation expansion
        text = self._expand_abbreviations(text)

        # Layer 4: synonym normalization
        working = self._normalize_synonyms(text)

        processing = np.nan
        setting = np.nan

        # Layer 5: processing-term extraction
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

        # Layer 6: setting-term extraction
        if self._SETTING_RE:
            smatch = self._SETTING_RE.search(working)
            if smatch:
                setting = smatch.group(1).lower()
                working = (working[: smatch.start()] + working[smatch.end():]).strip()

        # Layer 7: tier-1 patterns compiled from JSON
        if working:
            for category, pattern in self._TIER1_PATTERNS:
                m = pattern.search(working)
                if m:
                    return {
                        "one_health_category": category,
                        "one_health_term": m.group(0),
                        "one_health_confidence": 1.0,
                        "one_health_processing": processing,
                        "one_health_setting": setting,
                    }

        # Layer 8: rapidfuzz fuzzy fallback
        if _RAPIDFUZZ_AVAILABLE and self._fuzzy_corpus and working and len(working) > 2:
            result = _rfprocess.extractOne(
                working.lower(),
                self._fuzzy_corpus,
                scorer=_rfuzz.WRatio,
                score_cutoff=self._fuzzy_threshold,
            )
            if result:
                best_term, score, best_idx = result
                return {
                    "one_health_category": self._fuzzy_labels[best_idx],
                    "one_health_term": best_term,
                    "one_health_confidence": round(score / 100.0, 3),
                    "one_health_processing": processing,
                    "one_health_setting": setting,
                }

        # No match
        return {
            "one_health_category": "Unclassified",
            "one_health_term": np.nan,
            "one_health_confidence": 0.0,
            "one_health_processing": processing,
            "one_health_setting": setting,
        }
