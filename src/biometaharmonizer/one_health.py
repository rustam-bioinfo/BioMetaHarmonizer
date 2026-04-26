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
# FIX #5: removed over-broad scientific terms (biotechnology, microbiology,
# virology, medicine, sciences) from _INSTITUTION_KEYWORD_RE.

# FIX #6: requires >=3 tokens before comma so strain notations like
# 'Mus musculus, C57BL/6' are NOT flagged as institution addresses.
_HOST_COMMA_ADDRESS_RE = re.compile(
    r"^(?:\S+\s+){2,}\S+,\s*[A-Z][a-zA-Z]"
)


def _is_institution_host(text):
    """
    Return True if the host field value looks like an institution or
    address string rather than a biological organism name.
    """
    if _INSTITUTION_KEYWORD_RE.search(text):
        return True
    if _HOST_COMMA_ADDRESS_RE.match(text):
        return True
    return False


# ---------------------------------------------------------------------------
# Host name normalisation
# ---------------------------------------------------------------------------
# Submitters frequently append botanical authority abbreviations ('L.',
# 'Thunb.', 'DC.'), infraspecific ranks ('subsp.', 'var.', 'f.'),
# trailing rank indicators ('sp.', 'ssp.'), strain/colony suffixes, or
# parenthetical common-name qualifiers to scientific names.
# None of these are present as keys in host_to_category; stripping them
# before lookup recovers the canonical binomial.
#
# Examples that are fixed:
#   'Capsicum annuum L.'                          -> 'capsicum annuum'
#   'Tryonyx sinensis (Chinese soft-shell turtle)' -> 'tryonyx sinensis'
#   'Ursus sp.'                                    -> 'ursus'
#   'Macrotermes natalensis colony Mn106'          -> 'macrotermes natalensis'
#   'Bacillus thuringiensis subsp'                 -> 'bacillus thuringiensis'
#   'Galium aparine L.'                            -> 'galium aparine'

# Step 1: remove parenthetical qualifiers like '(common name)'
_HOST_PAREN_RE = re.compile(r"\s*\(.*?\)\s*")
# Step 2: remove trailing author abbreviations / infraspecific suffixes
# Order matters: longer patterns first.
_HOST_SUFFIX_RE = re.compile(
    r"\s+(?:"
    r"subsp\.?(?:\s+\S+)?"     # subsp. or subsp epithet
    r"|ssp\.?(?:\s+\S+)?"
    r"|var\.?(?:\s+\S+)?"
    r"|f\.(?:\s+\S+)?"
    r"|sp\.?"
    r"|colony\s+\S+"
    r"|strain\s+\S+"
    r"|[A-Z][a-z]*\."          # single-letter or abbreviated author (e.g. 'L.')
    r")$",
    re.IGNORECASE,
)


def _normalize_host_name(text):
    """
    Strip parenthetical qualifiers and taxonomic suffixes from a host
    field value and return the cleaned, lower-cased string for use as a
    key in host_to_category.

    Tries the fully stripped form first; if that is not useful (less than
    two tokens), returns only the cleaned lower-case original so the
    caller can decide what to do with it.
    """
    cleaned = _HOST_PAREN_RE.sub(" ", text).strip()
    # Iteratively strip trailing suffixes (handles stacked qualifiers)
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = _HOST_SUFFIX_RE.sub("", cleaned).strip()
    return cleaned.lower()


def _tier1_to_pattern(value):
    """
    Convert a tier1_patterns value to a compiled regex.

    The JSON stores tier1_patterns values as either:
      - a list of strings  -> joined with | and each term word-boundary wrapped
      - a string           -> used directly as a regex pattern

    Returns a compiled re.Pattern.
    """
    if isinstance(value, list):
        escaped = "|".join(re.escape(t) for t in value)
        pattern = r"\b(?:" + escaped + r")\b"
    else:
        pattern = value
    return re.compile(pattern, re.IGNORECASE)


# ---------------------------------------------------------------------------
# Expected output schema for _classify_text
# ---------------------------------------------------------------------------
_CLASSIFY_TEXT_KEYS = frozenset({
    "one_health_category",
    "one_health_term",
    "one_health_confidence",
    "one_health_processing",
    "one_health_setting",
})

# ---------------------------------------------------------------------------
# One Health category values
# ---------------------------------------------------------------------------
_VALID_CATEGORIES = frozenset({
    "Human",
    "Animal",
    "Aquatic",    # FIX #7
    "Wildlife",   # FIX #7
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
    loaded exclusively from one_health_dictionaries.json. The code
    contains only algorithmic logic.

    Layers (applied in order per text value):
      1. Null / empty detection
      2. Institution / culture collection guard  (applied in ALL entry-points)
      3. Abbreviation expansion
      4. Synonym normalization  (word-boundary safe)
      5. Processing-term extraction
      6. Setting-term extraction
      7. Tier-1 regex patterns (loaded from JSON, compiled at init)
      8. rapidfuzz fuzzy fallback (ontology_map corpus)

    Host-field resolution in _integrate_evidence (two-pass):
      The host_to_category dictionary lookup is performed BEFORE calling
      _classify_text so that Latinised binomials ('Bos taurus',
      'Gallus gallus', etc.) that have no match in tier1_patterns are
      correctly resolved via the dict without being incorrectly skipped.
      _normalize_host_name() strips botanical suffixes and parenthetical
      common-name qualifiers before the lookup.

    Field priority:
      isolation_source > host > env_medium > env_local_scale
      > env_broad_scale (supporting) > sample_type

    one_health_category is ALWAYS a string. It is never NaN.
    Values: Human | Animal | Aquatic | Wildlife | Plant | Food
            | Environmental | Lab | Unclassified

    Fuzzy threshold (default 92):
      WRatio >= 92 is required before a fuzzy match is accepted.
      This conservative default avoids false positives for short
      biological terms (e.g. 'rat' / 'cat' score ~87 on WRatio).
      Lower values increase recall at the cost of precision.

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

    # FIX #1 / FIX #8:
    # host is handled by a dedicated explicit branch in _integrate_evidence.
    # env_broad_scale demoted to _SUPPORTING_FIELDS.
    _DOMAIN_FIELDS = {"sample_type"}
    _SUPPORTING_FIELDS = {"env_broad_scale"}   # FIX #8
    _SPECIMEN_FIELDS = {"isolation_source", "env_medium", "env_local_scale"}

    NULL_PATTERNS = re.compile(
        r"^(missing|unknown|n/?a|not provided|not collected|not applicable|na|none|--)$",
        re.IGNORECASE,
    )

    def __init__(self, dictionary_path=None, fuzzy_threshold=92):  # FIX #13
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
        # FIX #10: pre-compile word-boundary patterns for synonym phrases
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
            raise ValueError(
                "classify_joint: series must share the same index."
            )
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

        FIX #12: output schema is validated; missing keys are filled with
        safe defaults rather than relying on pd.DataFrame to silently
        insert NaN for mismatched dict keys.
        """
        _required = ["one_health_category", "one_health_term", "one_health_confidence"]
        rows = []
        for v in series:
            result = self._classify_text(v)
            for key in _CLASSIFY_TEXT_KEYS:
                if key not in result:
                    logger.warning(
                        "_classify_text returned dict missing key %r; "
                        "patching with safe default.", key
                    )
                    result[key] = "Unclassified" if key == "one_health_category" else np.nan
            rows.append({k: result[k] for k in _required})
        df = pd.DataFrame(rows, index=series.index)
        return df

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

        FIX #11: unknown keyword arguments trigger a UserWarning.
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
        evidence_field = None   # FIX #3
        supporting_category = None   # FIX #8
        supporting_term = None
        supporting_field = None

        for field in self._FIELD_PRIORITY:
            val = getattr(row, field, None)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue
            val_str = str(val).strip()
            if not val_str or self.NULL_PATTERNS.match(val_str):
                continue

            # ----------------------------------------------------------
            # host field: institution guard then host_to_category lookup
            # BEFORE calling _classify_text.
            #
            # ROOT-CAUSE FIX: previously host_to_category was consulted
            # only AFTER checking `cat == 'Unclassified'` from
            # _classify_text.  Latinised binomials (Bos taurus, Gallus
            # gallus, Mus musculus …) have NO match in tier1_patterns
            # (which stores adjective-form terms: bovine, avian, murine)
            # so _classify_text returned Unclassified and the `continue`
            # guard fired before the dict was ever consulted.  Moving the
            # lookup here makes host_to_category the primary resolution
            # mechanism for the host field, as intended.
            # ----------------------------------------------------------
            if field == "host":
                if _is_institution_host(val_str):
                    logger.debug(
                        "host field looks like an institution/address, skipping: %r",
                        val_str[:80],
                    )
                    continue

                # Try exact match first, then normalised (suffix-stripped) form
                host_key = val_str.lower()
                host_cat = self._host_to_category.get(host_key)
                if host_cat is None:
                    norm_key = _normalize_host_name(val_str)
                    host_cat = self._host_to_category.get(norm_key)
                    lookup_term = norm_key
                else:
                    lookup_term = host_key

                if host_cat is not None:
                    if domain_category is None:
                        domain_category = host_cat
                        domain_term = lookup_term
                        domain_field = field
                    # Collect processing/setting from this field even
                    # on a dict hit, then move on.
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
                if domain_category is None:
                    domain_category = cat
                    # FIX #2: store resolved token, not raw val_str
                    domain_term = layer.get("one_health_term") or val_str
                    domain_field = field
                continue

            # ----------------------------------------------------------
            # All other fields: call _classify_text first
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

            # FIX #8: env_broad_scale demoted to supporting signal
            if field in self._SUPPORTING_FIELDS:
                if term_lower not in self._ambiguous_category_set:
                    if supporting_category is None:
                        supporting_category = cat
                        supporting_term = layer["one_health_term"]
                        supporting_field = field
                continue

            # generic domain field (currently only sample_type)
            if field in self._DOMAIN_FIELDS:
                if term_lower in self._ambiguous_category_set:
                    if evidence_term is None:
                        evidence_term = term_lower
                        evidence_field = field   # FIX #3
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
                    evidence_field = field   # FIX #3
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
            out["one_health_category"]     = domain_category
            out["one_health_term"]         = domain_term
            out["one_health_confidence"]   = 1.0 if supporting else 0.8
            out["one_health_source_field"] = domain_field
        elif specimen_category is not None:
            out["one_health_category"]     = specimen_category
            out["one_health_term"]         = specimen_term
            out["one_health_confidence"]   = specimen_confidence
            out["one_health_source_field"] = specimen_field
        elif specimen_term is not None or evidence_term is not None:
            # FIX #3: use evidence_field when specimen_field is None
            source = specimen_field if specimen_field is not None else evidence_field
            out["one_health_category"]     = "Unclassified"
            out["one_health_term"]         = specimen_term or evidence_term
            out["one_health_confidence"]   = specimen_confidence
            out["one_health_source_field"] = source
        else:
            # FIX #8: try supporting signal before setting inference
            if supporting_category is not None:
                out["one_health_category"]     = supporting_category
                out["one_health_term"]         = supporting_term
                out["one_health_confidence"]   = 0.5
                out["one_health_source_field"] = supporting_field
            else:
                setting_val = out.get("one_health_setting")
                if pd.notna(setting_val):
                    setting_lower = str(setting_val).lower()
                    inferred = self._setting_to_category.get(setting_lower)
                    if inferred:
                        out["one_health_category"]     = inferred
                        out["one_health_confidence"]   = self._setting_confidence.get(inferred, 0.4)
                        out["one_health_term"]         = setting_lower
                        out["one_health_source_field"] = "setting_inference"

        return out

    # ------------------------------------------------------------------
    # Text preprocessing helpers
    # ------------------------------------------------------------------

    def _expand_abbreviations(self, text):
        """
        Expand abbreviations token by token.

        FIX #9: split on whitespace, hyphens, and forward-slashes so
        that 'N/A', 'CSF-blood', 'env/food' are correctly decomposed.
        """
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
        """
        Replace synonym phrases with canonical forms using word-boundary
        anchors (FIX #10).
        """
        result = text
        for pattern, canonical in self._synonym_patterns:
            result = pattern.sub(canonical, result)
        return result

    # ------------------------------------------------------------------
    # Core single-value classification engine
    # ------------------------------------------------------------------

    def _classify_text(self, value):
        """
        Classify a single text value and return a dict with keys:
          one_health_category, one_health_term, one_health_confidence,
          one_health_processing, one_health_setting

        FIX #4: institution guard applied here so all entry-points
        (including legacy classify()) benefit from it.
        """
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

        # FIX #4 / #5 / #6: conservative institution guard
        if _is_institution_host(text):
            stripped = _INSTITUTION_KEYWORD_RE.sub("", text).strip(" .,;:-")
            if len(stripped) < 4:
                return {
                    "one_health_category": "Lab",
                    "one_health_term": text[:60],
                    "one_health_confidence": 0.9,
                    "one_health_processing": np.nan,
                    "one_health_setting": np.nan,
                }
            text = stripped if stripped else text

        # Layer 2b: JSON institution / culture collection patterns
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

        # Layer 4: synonym normalization (FIX #10)
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

        # Layer 7: tier-1 patterns
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

        # Layer 8: rapidfuzz fuzzy fallback (FIX #13: threshold 92)
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

        return {
            "one_health_category": "Unclassified",
            "one_health_term": np.nan,
            "one_health_confidence": 0.0,
            "one_health_processing": processing,
            "one_health_setting": setting,
        }
