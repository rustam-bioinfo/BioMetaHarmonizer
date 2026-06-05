"""
Shared synonym lookup table for BioMetaHarmonizer.

Both ingestion (Module 1) and key harmonization (Module 2) must resolve
attribute names to the same set of standard keys.  Previously each module
built its own lookup independently, with slightly different completeness.
This module is the single source of truth: both modules import
build_synonym_lookup() from here.

Resolution layers (applied in order):
  1. unified.json synonym lists  -- project-defined synonyms.  The position
     of each synonym in the list determines its collision priority: index 0
     wins over index 1, which wins over index 2, etc.  The standard_key
     itself is always rank 0 (highest priority).
  2. NCBI BioSample attribute XML (schemas/ncbi_attributes.xml) -- official
     NCBI HarmonizedName synonyms.  Present only after the optional
     build_ncbi_attribute_cache.py pre-build step; gracefully absent otherwise.
     NCBI XML entries only fill keys not already claimed by unified.json and
     receive no priority rank (defaulting to 999 at collision time).
  3. unified.json overrides re-applied -- project-defined mappings always
     win over NCBI XML entries for the same synonym key.

build_synonym_lookup() returns a tuple:
  synonym_map   -- {lowercased_synonym: standard_key}
  priority_map  -- {(standard_key, lowercased_synonym): int rank}

Both are cached for the lifetime of the process via lru_cache.
"""

import functools
import importlib.resources
import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path


logger = logging.getLogger(__name__)


def _schemas_dir() -> Path:
    """Return the path to the bundled schemas/ directory."""
    try:
        ref = importlib.resources.files("biometaharmonizer") / "schemas"
        return Path(str(ref))
    except (TypeError, ModuleNotFoundError):
        return Path(__file__).parent / "schemas"


@functools.lru_cache(maxsize=1)
def build_synonym_lookup() -> tuple[dict[str, str], dict[tuple, int]]:
    """
    Build and return synonym and priority lookup tables.

    Returns
    -------
    synonym_map : dict[str, str]
        Maps every lowercased synonym to its canonical standard key.
    priority_map : dict[tuple[str, str], int]
        Maps (standard_key, lowercased_synonym) to an integer rank.
        Lower rank = higher priority when two attributes collide on the
        same output column.  The standard_key itself is rank 0.  Synonyms
        are ranked 1..N in the order they appear in unified.json.  Any key
        not listed in unified.json (e.g. NCBI-only synonyms) is absent from
        this dict; callers should default to 999 on a missing key.

    Both dicts are computed once and cached; subsequent calls return the
    same objects without re-reading disk.
    """
    schemas = _schemas_dir()
    schema_path = schemas / "unified.json"
    xml_path = schemas / "ncbi_attributes.xml"

    synonym_map:      dict[str, str]   = {}
    priority_map:     dict[tuple, int] = {}
    unified_overrides: dict[str, str]  = {}

    # --- Layer 1: unified.json ---
    if schema_path.exists():
        try:
            with open(schema_path, "r", encoding="utf-8") as fh:
                schema = json.load(fh)
            for field in schema.get("fields", []):
                sk = field["standard_key"]
                # The standard key itself is rank 0 — highest priority.
                sk_lower = sk.lower()
                synonym_map[sk_lower] = sk
                unified_overrides[sk_lower] = sk
                priority_map[(sk, sk_lower)] = 0

                for rank, syn in enumerate(field.get("synonyms", []), start=1):
                    key = syn.strip().lower()
                    if key:
                        synonym_map[key] = sk
                        unified_overrides[key] = sk
                        priority_map[(sk, key)] = rank

        except Exception as exc:
            logger.warning("Could not load unified.json synonym layer: %s", exc)
    else:
        logger.debug("unified.json not found at %s; skipping layer 1.", schema_path)

    # --- Layer 2: NCBI BioSample attribute XML (optional) ---
    # Only fills keys not already claimed by unified.json.
    # NCBI entries receive no priority rank (callers default missing keys to 999).
    if xml_path.exists():
        try:
            tree = ET.parse(str(xml_path))
            root = tree.getroot()
            for attr in root.iter("Attribute"):
                hn_el = attr.find("HarmonizedName")
                if hn_el is None or not hn_el.text:
                    continue
                hn = hn_el.text.strip()
                if hn.lower() not in synonym_map:
                    synonym_map[hn.lower()] = hn
                for syn_el in attr.findall("Synonym"):
                    if syn_el.text and syn_el.text.strip():
                        k = syn_el.text.strip().lower()
                        if k not in synonym_map:
                            synonym_map[k] = hn
        except ET.ParseError as exc:
            logger.warning(
                "Could not parse NCBI attribute XML at %s; using unified.json only. "
                "Error: %s", xml_path, exc
            )
    else:
        logger.debug(
            "ncbi_attributes.xml not found at %s; layer 2 skipped.", xml_path
        )

    # --- Layer 3: re-apply unified.json overrides so project mappings always win ---
    synonym_map.update(unified_overrides)

    logger.debug(
        "Synonym lookup built: %d synonym entries, %d priority rules.",
        len(synonym_map), len(priority_map),
    )
    return synonym_map, priority_map
