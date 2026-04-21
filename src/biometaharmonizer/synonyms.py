"""
Shared synonym lookup table for BioMetaHarmonizer.

Both ingestion (Module 1) and key harmonization (Module 2) must resolve
attribute names to the same set of standard keys.  Previously each module
built its own lookup independently, with slightly different completeness.
This module is the single source of truth: both modules import
build_synonym_lookup() from here.

Resolution layers (applied in order, later layers win):
  1. unified.json synonym lists  -- project-defined synonyms.
  2. NCBI BioSample attribute XML (schemas/ncbi_attributes.xml) -- official
     NCBI HarmonizedName synonyms.  Present only after the optional
     build_ncbi_attribute_cache.py pre-build step; gracefully absent otherwise.

The returned dict maps every lowercased synonym to its canonical standard key.
The result is cached for the lifetime of the process via lru_cache so that
unified.json and ncbi_attributes.xml are read from disk only once.
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
def build_synonym_lookup() -> dict:
    """
    Build and return a {lowercased_synonym: standard_key} dict.

    Layer 1 (unified.json) is loaded first so that NCBI XML layer 2 can
    overwrite any conflicts with the authoritative NCBI mapping.

    The result is cached after the first call; subsequent calls return the
    same dict without re-reading disk.

    Returns
    -------
    dict
        Keys are lowercased synonym strings; values are canonical standard
        keys as defined in unified.json or NCBI HarmonizedName strings.
        Empty dict if neither schema file is present.
    """
    schemas = _schemas_dir()
    schema_path = schemas / "unified.json"
    xml_path = schemas / "ncbi_attributes.xml"

    lookup: dict[str, str] = {}

    # --- Layer 1: unified.json synonyms ---
    if schema_path.exists():
        try:
            with open(schema_path, "r", encoding="utf-8") as fh:
                schema = json.load(fh)
            for field in schema.get("fields", []):
                sk = field["standard_key"]
                lookup[sk.lower()] = sk
                for syn in field.get("synonyms", []):
                    syn_lower = syn.lower().strip()
                    if syn_lower:
                        lookup[syn_lower] = sk
        except Exception as exc:
            logger.warning("Could not load unified.json synonym layer: %s", exc)
    else:
        logger.debug("unified.json not found at %s; skipping layer 1.", schema_path)

    # --- Layer 2: NCBI BioSample attribute XML (optional) ---
    if xml_path.exists():
        try:
            tree = ET.parse(str(xml_path))
            root = tree.getroot()
            for attr in root.iter("Attribute"):
                hn_el = attr.find("HarmonizedName")
                if hn_el is None or not hn_el.text:
                    continue
                hn = hn_el.text.strip()
                lookup[hn.lower()] = hn
                for syn_el in attr.findall("Synonym"):
                    if syn_el.text and syn_el.text.strip():
                        lookup[syn_el.text.strip().lower()] = hn
        except ET.ParseError as exc:
            logger.warning(
                "Could not parse NCBI attribute XML at %s; using unified.json only. "
                "Error: %s", xml_path, exc
            )
    else:
        logger.debug(
            "ncbi_attributes.xml not found at %s; layer 2 skipped.", xml_path
        )

    logger.debug("Synonym lookup built: %d entries.", len(lookup))
    return lookup
