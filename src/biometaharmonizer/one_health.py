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


class OneHealthClassifier:
    """
    Module 5: One Health Categorization (Extended Architecture).

    Classifies records into standardized One Health tiers using
    deterministic, multi-layer semantic decomposition across six
    metadata fields without any LLM dependency.

    Layers (applied in order):
      1. Null / empty detection -> NaN
      2. Processing-term extraction (culture, DNA, isolate) -> decouples from source
      3. Setting-term extraction (hospital, farm, wwtp) -> stored separately
      4. Compiled Tier-1 regex patterns -> domain category
      5. Optional rapidfuzz fuzzy fallback against bundled dictionary terms

    Field priority for classify_multi_field():
      isolation_source > env_medium > env_local_scale > env_broad_scale > sample_type > host

    Backward-compatible public API
    --------------------------------
    classify(series)                -> pd.Series of one_health_category (legacy)
    classify_joint(iso, host)       -> pd.Series of one_health_category (legacy)
    classify_with_confidence(series)-> pd.DataFrame with 3 columns (legacy)
    classify_multi_field(**fields)  -> pd.DataFrame with 6 columns (extended)
    """

    NULL_PATTERNS = re.compile(
        r"^(missing|unknown|n/?a|not provided|not collected|not applicable|na|none|--)$",
        re.IGNORECASE,
    )

    TIER1_PATTERNS = (
        ("Environmental", re.compile(
            r"soil|\bwater\b|river|lake|sewage|wastewater|\bair\b|"
            r"rhizosphere|sediment|environment|dust|biofilm|"
            r"compost|manure|outdoor|indoor|cave|sand|"
            r"farm|field|forest|bark|\bmoss\b|lichen|peat|"
            r"wipe|swab.*surface|\bbaby\b.*wipe|"
            r"environmental\s+swab|env\s+swab|"
            r"drain\s+swab|sink\s+swab",
            re.IGNORECASE,
        )),
        ("Animal", re.compile(
            r"bovine|cattle|\bpig\b|swine|poultry|chicken|sheep|horse|"
            r"\bdog\b|\bcat\b|rodent|\bmouse\b|\brat\b|\bbird\b|\bfish\b|animal|"
            r"insect|\bbee\b|\bant\b|wasp|queen|colony|\blarva\b|larvae|"
            r"wild.caught|reared|invertebrate|arthropod|carcass|gut|flea|tick|"
            r"\bfly\b|\bmite\b|\bworm\b|nematode|cloacal",
            re.IGNORECASE,
        )),
        ("Human", re.compile(
            r"human|patient|clinical|homo\s+sapiens|person|"
            r"\bblood\b|urine|sputum|wound|stool|feces|fecal|faeces|"
            r"dental|plaque|biopsy|serum|plasma|\bcsf\b|cerebrospinal|"
            r"nasopharyngeal|throat|\bswab\b|abscess|\bbal\b|balf|"
            r"bronchoalveolar|bile|rectal|perirectal|urinary",
            re.IGNORECASE,
        )),
        ("Food", re.compile(
            r"food|\bmeat\b|\bmilk\b|cheese|vegetable|fruit|poultry product|"
            r"dairy|\begg\b|seafood|water supply|spice|grain|cereal|"
            r"flour|bread|fermented|beverage|pasta|sausage|ice.?cream|"
            r"noodle|rice|soy|tofu|sprout|produce|feed|additive|"
            r"supplement|\bhusk\b|\bbean\b|\bnut\b|\bherb\b|"
            r"slaughterhouse|abattoir|retail\s+food",
            re.IGNORECASE,
        )),
        ("Lab", re.compile(
            r"\blab\b|laboratory|atcc|reference\s+strain|"
            r"type\s+strain|synthetic|in\s+vitro|\bstrain\b.*collection",
            re.IGNORECASE,
        )),
    )

    _FIELD_PRIORITY = [
        "isolation_source",
        "env_medium",
        "env_local_scale",
        "env_broad_scale",
        "sample_type",
        "host",
    ]

    def __init__(self, dictionary_path=None, fuzzy_threshold=88):
        self._dicts = _load_dictionaries(dictionary_path)
        self._fuzzy_threshold = fuzzy_threshold

        proc_terms = self._dicts.get("processing_terms", {})
        if proc_terms:
            sorted_terms = sorted(proc_terms.keys(), key=len, reverse=True)
            pattern = r"\b(" + "|".join(re.escape(k) for k in sorted_terms) + r")\b"
            self._PROCESSING_RE = re.compile(pattern, re.IGNORECASE)
        else:
            self._PROCESSING_RE = None

        settings = self._dicts.get("setting_patterns", [])
        if settings:
            sorted_settings = sorted(settings, key=len, reverse=True)
            pattern = r"\b(" + "|".join(re.escape(s) for s in sorted_settings) + r")\b"
            self._SETTING_RE = re.compile(pattern, re.IGNORECASE)
        else:
            self._SETTING_RE = None

        self._proc_specimen_map = {
            k.lower(): v
            for k, v in self._dicts.get("processing_specimen_map", {}).items()
        }

        if _RAPIDFUZZ_AVAILABLE:
            ont_map = self._dicts.get("ontology_map", {})
            self._fuzzy_corpus = []
            self._fuzzy_labels = []
            for category, terms in ont_map.items():
                for term in terms:
                    self._fuzzy_corpus.append(term.lower())
                    self._fuzzy_labels.append(category)
        else:
            self._fuzzy_corpus = []
            self._fuzzy_labels = []

    def classify(self, series):
        """Legacy single-field classification. Returns pd.Series of one_health_category."""
        return series.apply(lambda v: self._classify_text(v)["one_health_category"])

    def _classify_single(self, value):
        """Internal helper retained for test compatibility."""
        return self._classify_text(value)["one_health_category"]

    def classify_joint(self, isolation_source_series, host_series):
        """
        Legacy joint classification.
        Classifies using isolation_source first; falls back to host
        where result is NaN or Unclassified.

        Parameters
        ----------
        isolation_source_series : pd.Series
        host_series             : pd.Series (must share same index)

        Returns
        -------
        pd.Series of one_health_category
        """
        if not isolation_source_series.index.equals(host_series.index):
            raise ValueError(
                "classify_joint: isolation_source_series and host_series must share "
                "the same index. Align them before calling this method."
            )
        result = self.classify(isolation_source_series).copy()
        fallback_mask = result.isna() | (result == "Unclassified")
        if fallback_mask.any():
            host_result = self.classify(host_series.loc[fallback_mask])
            result.loc[fallback_mask] = host_result
        return result

    def classify_with_confidence(self, series):
        """
        Legacy confidence output.

        Returns
        -------
        pd.DataFrame with columns:
          one_health_category, one_health_term, one_health_confidence
        """
        rows = series.apply(self._classify_text).tolist()
        df = pd.DataFrame(rows, index=series.index)
        return df[["one_health_category", "one_health_term", "one_health_confidence"]]

    def classify_multi_field(self, **fields):
        """
        Extended multi-field classification.
        Accepts up to six named pd.Series keyword arguments:
          isolation_source, host, sample_type,
          env_broad_scale, env_local_scale, env_medium

        Any subset of these fields may be passed; absent fields are treated
        as all-NaN. Fields are consumed in priority order (see _FIELD_PRIORITY).

        Parameters
        ----------
        **fields : pd.Series
            Named series corresponding to BioSample metadata columns.

        Returns
        -------
        pd.DataFrame with columns:
          one_health_category     - main domain label
          one_health_term         - matched text span
          one_health_confidence   - float 0-1
          one_health_processing   - detected processing type or NaN
          one_health_setting      - detected setting or NaN
          one_health_source_field - which input field produced the category
        """
        first = next((s for s in fields.values() if s is not None), None)
        if first is None:
            raise ValueError("classify_multi_field: no valid series provided.")
        idx = first.index

        aligned = {}
        for k in self._FIELD_PRIORITY:
            s = fields.get(k)
            if s is not None and not s.empty:
                aligned[k] = s.reindex(idx)
            else:
                aligned[k] = pd.Series(np.nan, index=idx, dtype=object)

        records = pd.DataFrame(aligned, index=idx)
        results = [
            self._integrate_evidence(row)
            for row in records.itertuples(index=False, name="Record")
        ]
        return pd.DataFrame(results, index=idx)

    def _integrate_evidence(self, row):
        """Integrate evidence from a single record across all six fields."""
        out = {
            "one_health_category": "Unclassified",
            "one_health_term": np.nan,
            "one_health_confidence": 0.0,
            "one_health_processing": np.nan,
            "one_health_setting": np.nan,
            "one_health_source_field": np.nan,
        }

        for field in self._FIELD_PRIORITY:
            val = getattr(row, field, None)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue
            val_str = str(val).strip()
            if not val_str or self.NULL_PATTERNS.match(val_str):
                continue

            layer = self._classify_text(val_str)

            if pd.notna(layer.get("one_health_processing")) and pd.isna(out["one_health_processing"]):
                out["one_health_processing"] = layer["one_health_processing"]

            if pd.notna(layer.get("one_health_setting")) and pd.isna(out["one_health_setting"]):
                out["one_health_setting"] = layer["one_health_setting"]

            cat = layer["one_health_category"]
            if cat not in (None, "Unclassified") and not (isinstance(cat, float) and np.isnan(cat)):
                if out["one_health_category"] == "Unclassified":
                    out["one_health_category"] = cat
                    out["one_health_term"] = layer["one_health_term"]
                    out["one_health_confidence"] = layer["one_health_confidence"]
                    out["one_health_source_field"] = field

        if out["one_health_category"] == "Unclassified" and pd.notna(out["one_health_setting"]):
            setting_lower = str(out["one_health_setting"]).lower()
            if setting_lower in {"hospital", "clinic", "icu", "ward", "nursing home"}:
                out["one_health_category"] = "Human"
                out["one_health_confidence"] = 0.5
                out["one_health_term"] = setting_lower
                out["one_health_source_field"] = "setting_inference"
            elif setting_lower in {"farm", "abattoir", "slaughterhouse"}:
                out["one_health_category"] = "Environmental"
                out["one_health_confidence"] = 0.4
                out["one_health_term"] = setting_lower
                out["one_health_source_field"] = "setting_inference"

        if out["one_health_category"] == "Unclassified":
            if pd.notna(out["one_health_processing"]) or pd.notna(out["one_health_setting"]):
                out["one_health_category"] = np.nan

        return out

    def _classify_text(self, value):
        """
        Core decomposition engine for a single text value.

        Returns a dict with keys:
          one_health_category, one_health_term, one_health_confidence,
          one_health_processing, one_health_setting
        """
        empty = {
            "one_health_category": np.nan,
            "one_health_term": np.nan,
            "one_health_confidence": 0.0,
            "one_health_processing": np.nan,
            "one_health_setting": np.nan,
        }

        if value is None or (isinstance(value, float) and np.isnan(value)):
            return empty

        text = str(value).strip()
        if not text:
            return empty
        if self.NULL_PATTERNS.match(text):
            return empty

        processing = np.nan
        setting = np.nan
        working = text

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
                        + working[pmatch.end() :].strip()
                    ).strip()
                else:
                    working = (working[: pmatch.start()] + working[pmatch.end() :]).strip()

        if self._SETTING_RE:
            smatch = self._SETTING_RE.search(working)
            if smatch:
                setting = smatch.group(1).lower()
                working = (working[: smatch.start()] + working[smatch.end() :]).strip()

        if working:
            for category, pattern in self.TIER1_PATTERNS:
                m = pattern.search(working)
                if m:
                    return {
                        "one_health_category": category,
                        "one_health_term": m.group(0),
                        "one_health_confidence": 1.0,
                        "one_health_processing": processing,
                        "one_health_setting": setting,
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
                category = self._fuzzy_labels[best_idx]
                confidence = round(score / 100.0, 3)
                return {
                    "one_health_category": category,
                    "one_health_term": best_term,
                    "one_health_confidence": confidence,
                    "one_health_processing": processing,
                    "one_health_setting": setting,
                }

        if pd.notna(processing) or pd.notna(setting):
            return {
                "one_health_category": np.nan,
                "one_health_term": np.nan,
                "one_health_confidence": 0.0,
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
