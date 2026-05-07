#!/usr/bin/env python3
"""
build_dictionaries.py

Builds an enriched one_health_dictionaries.json by querying:
  1. OLS4 API  - ENVO, FoodOn, UBERON, Plant Ontology
  2. NCBI Taxonomy local dump - host_to_category for vertebrates + plants
  3. UMLS API  - synonym expansion (optional, requires API key)

The hand-curated base file is loaded first. Any key present in the base
always wins over ontology-derived data (merge strategy: base_wins).

Usage
-----
  python scripts/build_dictionaries.py \\
      --base    src/biometaharmonizer/schemas/one_health_dictionaries.json \\
      --output  src/biometaharmonizer/schemas/one_health_dictionaries.json

  # Use a pre-downloaded taxdmp.zip to skip the ~65 MB download:
  python scripts/build_dictionaries.py --taxdmp /path/to/taxdmp.zip

  # Skip taxonomy entirely:
  python scripts/build_dictionaries.py --skip-ncbi

Dependencies (all standard or already in requirements.txt):
  requests>=2.28
  pandas>=1.5

The script is intentionally standalone - no imports from biometaharmonizer.
"""

import argparse
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build_dictionaries")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OLS_BASE  = "https://www.ebi.ac.uk/ols4/api"
UMLS_BASE = "https://uts-ws.nlm.nih.gov/rest"

NCBI_TAXDMP_URL = "https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdmp.zip"

# OLS4: map short IDs to full purl IRIs
OLS_IRI_PREFIXES = {
    "ENVO":   "http://purl.obolibrary.org/obo/ENVO_",
    "FOODON": "http://purl.obolibrary.org/obo/FOODON_",
    "UBERON": "http://purl.obolibrary.org/obo/UBERON_",
    "PO":     "http://purl.obolibrary.org/obo/PO_",
}

# OLS ontology ids -> One Health category -> list of seed term IRIs
#
# Removed seeds that return 0 descendants from OLS4 hierarchicalDescendants
# because they use OWL restriction-based (not subClassOf) hierarchies:
#
#   ENVO:00005772  habitat       - subclasses linked via 'overlaps' not subClassOf
#   ENVO:00002042  surface water - partOf hierarchy, not traversed by OLS4
#   ENVO:00000375  biofilm       - no real subClassOf tree; hand-curated in base dict
#
# Removed FoodOn seeds that are either obsolete or return 0 descendants:
#   FOODON:03310113  aquatic food product - suspected obsolete IRI
#   FOODON:03411347  dairy product        - suspected obsolete IRI
# These food subcategories are already covered by FOODON:00001002 (food product)
# and FOODON:03400361 (food material) which harvest their full subtrees.
OLS_ONTOLOGY_MAP = {
    "envo": {
        "Environmental": [
            "ENVO:00000428",   # biome
            "ENVO:00010483",   # environmental material
            "ENVO:01000254",   # anthropogenic environment
            "ENVO:01001110",   # ecosystem
            "ENVO:00000063",   # soil
            "ENVO:00000015",   # ocean
            "ENVO:00000873",   # freshwater body
            "ENVO:00000134",   # sediment
            "ENVO:00002006",   # water
        ],
    },
    "foodon": {
        "Food": [
            "FOODON:00001002",  # food product
            "FOODON:03400361",  # food material
            "FOODON:00001709",  # animal food product
            "FOODON:03420194",  # plant food product
        ],
    },
    "uberon": {
        "_anatomy": [
            "UBERON:0000465",  # material anatomical entity
        ],
    },
    "po": {
        "Plant": [
            "PO:0025131",  # plant anatomical entity
        ],
    },
}

# NCBI Taxonomy node IDs -> One Health category
#
# IMPORTANT - only clades that map cleanly to a SINGLE One Health category
# belong here. Fungi (txid4751) are intentionally excluded: their category
# is context-dependent (Environmental pathogen, Food spoilage, Animal/Human
# mycosis) and cannot be resolved from taxonomy alone. "Lab" is a
# text-signal category only, detected via ontology_map["Lab"] keywords
# (e.g. "in vitro", "ATCC", "type strain") -- never via taxon subtree.
NCBI_TAXON_ROOTS = {
    9606:  "Human",    # Homo sapiens (exact match only, no subtree walk)
    40674: "Animal",   # Mammalia
    8782:  "Animal",   # Aves
    8504:  "Animal",   # Reptilia
    8292:  "Animal",   # Amphibia
    7776:  "Animal",   # Chondrichthyes
    7898:  "Animal",   # Actinopterygii
    6656:  "Animal",   # Arthropoda
    6447:  "Animal",   # Mollusca
    6231:  "Animal",   # Nematoda
    6340:  "Animal",   # Annelida
    7586:  "Animal",   # Echinodermata
    6073:  "Animal",   # Cnidaria
    6040:  "Animal",   # Porifera
    33090: "Plant",    # Viridiplantae
    2763:  "Plant",    # Rhodophyta (red algae)
    3041:  "Plant",    # Chlorophyta (green algae, non-land)
    2870:  "Plant",    # Phaeophyceae (brown algae / kelp)
}

# name_class values from names.dmp that are useful for host matching.
# Excluded: 'synonym', 'authority', 'blast name', 'in-part', 'includes',
# 'type material', 'anamorph', 'teleomorph' - too technical or cause
# false positives against BioSample free-text metadata.
NAMES_DMP_KEEP_CLASSES = {
    "scientific name",
    "common name",
    "genbank common name",
    "equivalent name",
}

# Substring sets to classify UBERON anatomy terms
UBERON_HUMAN_EXCLUSIVE = {
    "cerebrospinal fluid", "pleural fluid", "peritoneal fluid",
    "synovial fluid", "amniotic fluid", "dialysate",
    "bronchoalveolar lavage", "sputum", "dental plaque",
    "catheter", "central venous",
}

UBERON_ANIMAL_EXCLUSIVE = {
    "rumen", "reticulum", "omasum", "abomasum",
    "gizzard", "proventriculus", "crop",
    "cloaca", "swim bladder", "gill",
    "hemolymph", "exoskeleton",
}

# ---------------------------------------------------------------------------
# Compiled patterns for _clean_ols_term()
# ---------------------------------------------------------------------------

_RE_LANG_TAG  = re.compile(r'\([^)]*,\s*(exact|related|broad|narrow)\s*\)\s*$', re.IGNORECASE)
_RE_SCOPE_TAG = re.compile(r'\(\s*(exact|related|broad|narrow)\s*\)\s*$', re.IGNORECASE)
_RE_GS1_GPC   = re.compile(r'^\d+\s*-\s*.+\(gs1 gpc\)\s*$', re.IGNORECASE)

# Regulatory catalogue codes from FoodOn synonyms.
# These are classification scheme artefacts, not biological terms that can
# be matched against BioSample free-text metadata.
#
# Pattern families covered:
#   EFSA FoodEx2  : "12640 - bay leaves, dry (efsa foodex2)"
#   EC codes      : "0900000 - 9. sugar plants (ec)"
#   EuroFIR       : "grain or grain product (eurofir)"
#   EFG (EFSA)    : "33  products for special nutritional use (efg)"
#   CIAA          : "CIAA fruits and vegetables"
#   CCFAC         : "CCFAC bakery wares"
#   Codex         : "formulation agent (codex)", "food preservative (codex)"
#   Other (...source) catalogue synonyms caught by the generic rule below
#
# Rule order matters: most-specific patterns first, generic catch-all last.
_RE_REGULATORY_CATALOGUE = re.compile(
    r"""
    (?:
        # Numeric code + dash + description + parenthesised source tag
        # e.g.  "12640 - bay leaves, dry (efsa foodex2)"
        #        "0900000 - 9. sugar plants (ec)"
        ^\s*\d[\d\s]*\s*-\s*.+\(\s*(?:efsa\s+foodex2?|ec|eurofir|efg|codex|ciaa|ccfac|gs1|gpc)[^)]*\)\s*$
        |
        # Numeric code + dash + description, no source tag but format is clearly a catalogue entry
        # e.g.  "05110 - sunflower shoots and sprouts (efsa foodex2)"
        # (already caught above, but guard for tag-less variants)
        ^\s*0\d{4,}\s*-\s*.+$
        |
        # Parenthesised source tag at end, no leading code
        # e.g.  "grain or grain product (eurofir)"
        #        "formulation agent (codex)"
        #        "33  products for special nutritional use (efg)"
        .+\(\s*(?:efsa\s+foodex2?|eurofir|efg|codex|ciaa|ccfac|gs1\s+gpc)[^)]*\)\s*$
        |
        # CIAA / CCFAC bare prefix (no parentheses)
        # e.g.  "CIAA edible ices", "CCFAC cereals and cereal products"
        ^\s*(?:CIAA|CCFAC)\s+.+$
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})


def _get(url, params=None, retries=3, backoff=2.0, as_text=False):
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=30)
            if r.status_code == 429:
                wait = backoff * (attempt + 1) * 2
                log.warning("Rate limited, waiting %.0fs", wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.text if as_text else r.json()
        except requests.RequestException as exc:
            if attempt == retries - 1:
                log.error("Failed GET %s: %s", url, exc)
                return None
            time.sleep(backoff)
    return None


# ---------------------------------------------------------------------------
# OLS4 helpers
# ---------------------------------------------------------------------------

def _short_id_to_iri(short_id):
    """
    Convert 'ENVO:00000428' -> 'http://purl.obolibrary.org/obo/ENVO_00000428'
    """
    prefix, local = short_id.split(":", 1)
    base_iri = OLS_IRI_PREFIXES.get(prefix.upper())
    if not base_iri:
        raise ValueError(f"Unknown IRI prefix: {prefix}")
    return base_iri + local


def _clean_ols_term(term):
    """
    Clean and validate a raw OLS term string before adding it to the dictionary.

    Returns the cleaned string, or None if the term should be discarded.

    Rules applied in order:
      1. Non-ASCII characters -> discard
      2. Language-tagged synonyms: '...(spanish, exact)' -> discard
      3. OBO scope tag leak: '...(exact)' -> strip suffix
      4. GS1/GPC retail catalogue codes -> discard
      5. Regulatory catalogue codes (EFSA FoodEx2, EC, EuroFIR, EFG,
         CIAA, CCFAC, Codex) -> discard
      6. Too short after cleaning (<= 2 chars) -> discard
    """
    if not term or not isinstance(term, str):
        return None

    # Rule 1: non-ASCII
    try:
        term.encode("ascii")
    except UnicodeEncodeError:
        return None

    # Rule 2: language-tagged synonyms
    if _RE_LANG_TAG.search(term):
        return None

    # Rule 3: bare OBO scope tag leak
    term = _RE_SCOPE_TAG.sub("", term).strip()

    # Rule 4: GS1/GPC retail catalogue codes (kept separate for clarity)
    if _RE_GS1_GPC.match(term):
        return None

    # Rule 5: regulatory catalogue codes (EFSA, EC, EuroFIR, CIAA, CCFAC, Codex)
    if _RE_REGULATORY_CATALOGUE.match(term):
        return None

    # Rule 6: too short after cleaning
    if len(term) <= 2:
        return None

    return term


def ols_descendants(ontology, short_id, max_terms=2000):
    """
    Fetch all hierarchicalDescendant term labels + exact synonyms for a
    given short ID from OLS4.

    OLS4 endpoint:
      GET /ontologies/{onto}/terms/{double_encoded_iri}/hierarchicalDescendants

    IRI must be double URL-encoded as a path segment.
    Response: _embedded.terms[].label  +  annotation.hasExactSynonym[]

    Only hasExactSynonym / has_exact_synonym are collected. Broad, narrow,
    related, and unscoped synonym lists are intentionally excluded to avoid
    false-positive category assignments.

    All collected strings pass through _clean_ols_term() before storage.

    Warns if:
      - zero terms are returned (likely obsolete IRI or restriction-based hierarchy)
      - the max_terms ceiling is hit (results may be truncated)
    """
    iri = _short_id_to_iri(short_id)
    encoded = quote(quote(iri, safe=""), safe="")
    url = f"{OLS_BASE}/ontologies/{ontology}/terms/{encoded}/hierarchicalDescendants"

    terms = []
    page = 0
    page_size = 500

    while len(terms) < max_terms:
        data = _get(url, params={"size": page_size, "page": page})
        if not data:
            break

        embedded = data.get("_embedded", {})
        items = embedded.get("terms", [])
        if not items:
            break

        for item in items:
            label = item.get("label", "")
            cleaned = _clean_ols_term(label)
            if cleaned:
                terms.append(cleaned)

            annotation = item.get("annotation", {})
            for syn in (
                annotation.get("hasExactSynonym", [])
                + annotation.get("has_exact_synonym", [])
            ):
                if syn and isinstance(syn, str):
                    cleaned = _clean_ols_term(syn)
                    if cleaned:
                        terms.append(cleaned)

        page_info = data.get("page", {})
        total_pages = page_info.get("totalPages", 1)
        page += 1
        if page >= total_pages:
            break
        time.sleep(0.15)

    unique_terms = list(dict.fromkeys(terms))

    if len(unique_terms) == 0:
        log.warning(
            "ZERO terms returned for %s:%s - IRI may be obsolete or use "
            "a non-subClassOf hierarchy not traversed by OLS4 hierarchicalDescendants",
            ontology.upper(), short_id,
        )
    elif len(terms) >= max_terms:
        log.warning(
            "max_terms ceiling (%d) hit for %s:%s - results may be truncated; "
            "consider raising max_terms",
            max_terms, ontology.upper(), short_id,
        )

    return unique_terms


def fetch_ols_terms():
    log.info("Fetching OLS4 terms...")
    result = {}
    anatomy_all = []

    for ontology, category_map in OLS_ONTOLOGY_MAP.items():
        for category, short_ids in category_map.items():
            for short_id in short_ids:
                if category == "_anatomy":
                    log.info("  UBERON %s (anatomy)", short_id)
                    terms = ols_descendants(ontology, short_id, max_terms=3000)
                    anatomy_all.extend(terms)
                    log.info("    -> %d anatomy terms", len(terms))
                else:
                    log.info("  %s %s -> %s", ontology.upper(), short_id, category)
                    terms = ols_descendants(ontology, short_id)
                    result.setdefault(category, []).extend(terms)
                    log.info("    -> %d terms", len(terms))

    # Classify anatomy terms into human / animal / ambiguous
    human_terms, animal_terms, ambiguous_terms = [], [], []
    for term in anatomy_all:
        if any(excl in term for excl in UBERON_HUMAN_EXCLUSIVE):
            human_terms.append(term)
        elif any(excl in term for excl in UBERON_ANIMAL_EXCLUSIVE):
            animal_terms.append(term)
        else:
            ambiguous_terms.append(term)

    result["_uberon_human"]     = human_terms
    result["_uberon_animal"]    = animal_terms
    result["_uberon_ambiguous"] = ambiguous_terms

    # Log post-collection unique counts per category
    for cat in ("Environmental", "Food", "Plant"):
        raw = result.get(cat, [])
        unique = len(set(raw))
        log.info("OLS terms collected: %s -> %d raw / %d unique", cat, len(raw), unique)

    return result


# ---------------------------------------------------------------------------
# NCBI local taxonomy dump helpers
# ---------------------------------------------------------------------------

def _download_taxdmp(dest_dir):
    """
    Download taxdmp.zip from NCBI FTP into dest_dir.
    Returns path to the downloaded zip file.
    """
    zip_path = Path(dest_dir) / "taxdmp.zip"
    log.info("Downloading taxdmp.zip from %s ...", NCBI_TAXDMP_URL)
    with requests.get(NCBI_TAXDMP_URL, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):  # 1 MB chunks
                f.write(chunk)
                downloaded += len(chunk)
        log.info("Downloaded %.1f MB", downloaded / 1e6)
    return zip_path


def _extract_taxdmp(zip_path, dest_dir):
    """
    Extract names.dmp and nodes.dmp from taxdmp.zip into dest_dir.
    Returns (names_path, nodes_path).
    """
    log.info("Extracting names.dmp and nodes.dmp ...")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extract("names.dmp", dest_dir)
        zf.extract("nodes.dmp", dest_dir)
    return Path(dest_dir) / "names.dmp", Path(dest_dir) / "nodes.dmp"


def _parse_names_dmp(names_path):
    """
    Parse names.dmp into a DataFrame with columns:
        tax_id (Int64), name_txt (str), name_class (str)

    Only rows whose name_class is in NAMES_DMP_KEEP_CLASSES are kept.
    name_txt is lowercased for case-insensitive downstream matching.
    """
    log.info("Parsing names.dmp ...")
    df = pd.read_csv(
        names_path,
        sep=r"\t\|\t",
        engine="python",
        header=None,
        names=["tax_id", "name_txt", "unique_name", "name_class"],
        dtype=str,
    )
    df["name_class"] = df["name_class"].str.replace(r"\t\|$", "", regex=True)
    df = df.apply(lambda col: col.str.strip())
    df["tax_id"] = df["tax_id"].astype("Int64")
    df = df[df["name_class"].isin(NAMES_DMP_KEEP_CLASSES)].copy()
    df["name_txt"] = df["name_txt"].str.lower()
    log.info("  %d name rows kept (after class filter)", len(df))
    return df[["tax_id", "name_txt"]]


def _parse_nodes_dmp(nodes_path):
    """
    Parse nodes.dmp into a parent->children mapping.
    Returns dict[int, list[int]].
    """
    log.info("Parsing nodes.dmp ...")
    nodes = pd.read_csv(
        nodes_path,
        sep=r"\t\|\t",
        engine="python",
        header=None,
        usecols=[0, 1],
        names=["tax_id", "parent_tax_id"],
        dtype=str,
    )
    nodes = nodes.apply(lambda col: col.str.strip())
    nodes["tax_id"]        = nodes["tax_id"].astype("Int64")
    nodes["parent_tax_id"] = nodes["parent_tax_id"].astype("Int64")
    log.info("  %d nodes loaded", len(nodes))

    children = defaultdict(list)
    for row in nodes.itertuples(index=False):
        tid = int(row.tax_id)
        pid = int(row.parent_tax_id)
        if tid != pid:  # root node points to itself
            children[pid].append(tid)
    return children


def _bfs_subtree(root_id, children):
    """
    Iterative BFS from root_id over the children dict.
    Returns frozenset of all descendant tax_ids (including root_id).
    """
    visited = set()
    queue = [root_id]
    while queue:
        node = queue.pop()
        if node in visited:
            continue
        visited.add(node)
        queue.extend(children.get(node, []))
    return frozenset(visited)


def _resolve_taxdmp_path(taxdmp_arg):
    """
    Resolve the path to names.dmp and nodes.dmp from the --taxdmp argument.

    Accepts:
      - a .zip file path  -> extract to a temp dir
      - a directory path  -> expect names.dmp + nodes.dmp inside
      - None              -> download taxdmp.zip to a temp dir

    Returns (names_path, nodes_path, tmp_dir_to_cleanup_or_None).
    """
    if taxdmp_arg is None:
        tmp = tempfile.mkdtemp(prefix="taxdmp_")
        zip_path = _download_taxdmp(tmp)
        names_path, nodes_path = _extract_taxdmp(zip_path, tmp)
        return names_path, nodes_path, tmp

    p = Path(taxdmp_arg)
    if p.is_file() and p.suffix == ".zip":
        tmp = tempfile.mkdtemp(prefix="taxdmp_")
        names_path, nodes_path = _extract_taxdmp(p, tmp)
        return names_path, nodes_path, tmp

    if p.is_dir():
        names_path = p / "names.dmp"
        nodes_path = p / "nodes.dmp"
        if not names_path.exists() or not nodes_path.exists():
            log.error("--taxdmp dir %s must contain names.dmp and nodes.dmp", p)
            sys.exit(1)
        return names_path, nodes_path, None

    log.error("--taxdmp must be a .zip file or a directory; got: %s", taxdmp_arg)
    sys.exit(1)


def build_host_map_from_dump(taxdmp_arg=None):
    """
    Build host_to_category dict from the local NCBI taxonomy dump.

    Algorithm:
      1. Resolve / download names.dmp + nodes.dmp
      2. Parse names.dmp, keep scientific name / common name /
         genbank common name / equivalent name rows
      3. Parse nodes.dmp -> parent->children dict
      4. For each root in NCBI_TAXON_ROOTS (except 9606 Human):
         BFS to collect all descendant tax_ids
      5. Filter names df to those tax_ids, emit name_txt -> category
         Only names with 1-3 tokens are kept (binomials, trinomials, and
         short common names); longer strings are strain/haplotype descriptors
         that never appear in BioSample host fields.
      6. Add hard-coded Human entries for tax_id 9606

    Returns dict: name_lower -> category
    """
    log.info("Building host map from local taxonomy dump...")

    names_path, nodes_path, tmp_dir = _resolve_taxdmp_path(taxdmp_arg)

    try:
        names_df = _parse_names_dmp(names_path)
        children = _parse_nodes_dmp(nodes_path)

        # Build a set of all tax_ids present in names.dmp for fast membership checks
        all_name_ids = set(names_df["tax_id"].dropna().astype(int))

        host_map = {
            "homo sapiens": "Human",
            "human":        "Human",
            "man":          "Human",
        }

        for root_id, category in NCBI_TAXON_ROOTS.items():
            if root_id == 9606:
                continue

            subtree_ids = _bfs_subtree(root_id, children)
            # Intersect with IDs that actually have names
            name_ids_in_subtree = subtree_ids & all_name_ids

            subset = names_df[names_df["tax_id"].isin(name_ids_in_subtree)]
            count = 0
            for name in subset["name_txt"]:
                if name and len(name) >= 3 and len(name.split()) <= 3:
                    host_map[name] = category
                    count += 1

            log.info(
                "  taxon %d (%s): %d nodes in subtree, %d name entries",
                root_id, category, len(subtree_ids), count,
            )

        log.info("host_to_category: %d entries total", len(host_map))
        return host_map

    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            log.info("Cleaned up temp dir %s", tmp_dir)


# ---------------------------------------------------------------------------
# UMLS helpers (optional)
# ---------------------------------------------------------------------------

def umls_get_tgt(api_key):
    r = requests.post(
        "https://utslogin.nlm.nih.gov/cas/v1/api-key",
        data={"apikey": api_key},
        timeout=15,
    )
    r.raise_for_status()
    import re as _re
    m = _re.search(r'action="(.*?)"', r.text)
    if not m:
        raise RuntimeError("Could not parse UMLS TGT")
    return m.group(1)


def umls_service_ticket(tgt_url):
    r = requests.post(
        tgt_url,
        data={"service": "http://umlsks.nlm.nih.gov"},
        timeout=15,
    )
    r.raise_for_status()
    return r.text.strip()


def umls_synonyms_for_cui(cui, tgt_url):
    st   = umls_service_ticket(tgt_url)
    data = _get(
        f"{UMLS_BASE}/content/current/CUI/{cui}/atoms",
        params={"ticket": st, "language": "ENG", "pageSize": 100},
    )
    if not data:
        return []
    return [
        atom["name"].lower()
        for atom in data.get("result", [])
        if atom.get("name")
    ]


UMLS_SPECIMEN_CUIS = {
    "C0005767": "blood",
    "C0042036": "urine",
    "C0038569": "sputum",
    "C0007555": "cerebrospinal fluid",
    "C0205189": "pleural fluid",
    "C0003967": "ascitic fluid",
    "C0039981": "synovial fluid",
    "C0006252": "bronchial lavage",
    "C0444941": "wound",
    "C0000735": "abscess",
    "C0032227": "pus",
    "C0015411": "feces",
    "C0521481": "rectal swab",
    "C0029001": "oral swab",
    "C0042048": "vaginal swab",
    "C0877612": "nasal swab",
    "C0586478": "throat swab",
}


def fetch_umls_synonyms(api_key):
    log.info("Fetching UMLS synonyms...")
    tgt_url     = umls_get_tgt(api_key)
    synonym_map = {}
    for cui, canonical in UMLS_SPECIMEN_CUIS.items():
        syns = umls_synonyms_for_cui(cui, tgt_url)
        if syns:
            synonym_map[canonical] = syns
            log.info("  %s (%s): %d synonyms", canonical, cui, len(syns))
        time.sleep(0.1)
    return synonym_map


# ---------------------------------------------------------------------------
# Collision resolution
# ---------------------------------------------------------------------------

def _resolve_collisions(base):
    """
    Detect terms that appear in multiple One Health categories or conflict
    with authoritative dictionary sections, and record them in
    base["ambiguous_category_terms"] instead of silently keeping them in
    whichever category happened to be populated first.

    Two collision types are handled:

    1. Intra-ontology_map: same term string present in 2+ *distinct* category
       lists (e.g. "blood" in both Food and Animal). Duplicate entries for
       the same category (caused by overlapping OLS4 seed subtrees) are
       deduplicated first and do NOT count as a collision.

    2. Cross-section (authoritative only): a term in ontology_map also exists
       in host_to_category, unambiguous_human_terms, or
       unambiguous_animal_terms.

    NOTE: ambiguous_specimen_terms is intentionally NOT treated as an
    authoritative conflict source. It is a tiebreaker list used during
    classification when no domain signal is present. A term that is already
    uniquely placed in one ontology_map category should NOT be evicted just
    because it also appears in ambiguous_specimen_terms — doing so causes
    correctly categorized entries (e.g. Food, Environmental, Plant ontology
    terms like "fruit", "extract", "wash") to be silently suppressed and
    produce Unclassified outputs.

    In both collision cases the term is removed from the ontology_map
    category list(s) and added to ambiguous_category_terms with the list of
    conflicting sources.

    Terms that were present in the hand-curated base ontology_map before this
    build run are exempt (base_wins: if a human already decided the category,
    there is no ambiguity).

    Returns a stats dict that is attached to _metadata["collision_stats"].
    """
    ont_map   = base.get("ontology_map", {})
    ambiguous = base.setdefault("ambiguous_category_terms", {})

    # Build inverted index: term -> [categories] (may contain duplicates when
    # multiple OLS4 seeds for the same category share overlapping subtrees).
    term_to_cats = {}
    for cat, terms in ont_map.items():
        for t in terms:
            term_to_cats.setdefault(t, []).append(cat)

    # Authoritative cross-section lookup sets.
    # ambiguous_specimen_terms is deliberately excluded — see docstring.
    host_keys  = set(base.get("host_to_category", {}).keys())
    human_set  = set(t.lower() for t in base.get("unambiguous_human_terms",  []))
    animal_set = set(t.lower() for t in base.get("unambiguous_animal_terms", []))

    intra_count = 0
    cross_count = 0

    for term, cats in term_to_cats.items():
        # Deduplicate category list while preserving order. Multiple identical
        # categories arise when overlapping ENVO seed subtrees return the same
        # term; this is NOT a real collision and must not trigger eviction.
        unique_cats = list(dict.fromkeys(cats))

        conflicts = list(unique_cats)

        # Only authoritative sections trigger cross-section eviction
        if term in host_keys:
            conflicts.append("host_to_category")
        if term in human_set:
            conflicts.append("unambiguous_human_terms")
        if term in animal_set:
            conflicts.append("unambiguous_animal_terms")

        has_intra = len(unique_cats) > 1
        has_cross = len(conflicts) > len(unique_cats)

        if not has_intra and not has_cross:
            continue

        # base_wins: if term was hand-curated into exactly one ontology_map
        # category and has no authoritative cross-section conflict, leave it alone
        if not has_cross and len(unique_cats) == 1:
            continue

        # Record in ambiguous_category_terms (merge if already present)
        existing = ambiguous.get(term, [])
        merged   = list(dict.fromkeys(existing + conflicts))
        ambiguous[term] = merged

        # Remove from all ontology_map category lists (use unique_cats to
        # avoid redundant .remove() calls on already-cleaned lists)
        for cat in unique_cats:
            try:
                ont_map[cat].remove(term)
            except ValueError:
                pass

        if has_intra:
            intra_count += 1
            log.warning(
                "COLLISION intra-ontology_map: '%s' in %s -> moved to ambiguous_category_terms",
                term, unique_cats,
            )
        if has_cross:
            cross_count += 1
            log.warning(
                "COLLISION cross-section: '%s' conflicts with %s -> moved to ambiguous_category_terms",
                term, [c for c in conflicts if c not in unique_cats],
            )

    log.info(
        "Collision resolution: %d intra-ontology_map, %d cross-section, %d total ambiguous terms",
        intra_count, cross_count, len(ambiguous),
    )

    return {
        "total_ambiguous_terms": len(ambiguous),
        "intra_ontology_map":    intra_count,
        "cross_section":         cross_count,
    }


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

def merge_into_base(base, ols_terms, ncbi_host_map, umls_synonyms):
    log.info("Merging ontology data into base dictionary...")

    # ontology_map
    ont_map = base.setdefault("ontology_map", {})
    for category in ("Environmental", "Food", "Plant"):
        existing  = set(ont_map.get(category, []))
        new_terms = [
            t for t in ols_terms.get(category, [])
            if t not in existing and len(t) >= 3
        ]
        if new_terms:
            ont_map.setdefault(category, []).extend(new_terms)
            post_dedup = len(set(ont_map[category]))
            log.info("  ontology_map[%s] +%d terms -> %d unique total", category, len(new_terms), post_dedup)

    # host_to_category
    host_map   = base.setdefault("host_to_category", {})
    base_hosts = {k.lower() for k in host_map}
    added      = sum(
        1 for name, cat in ncbi_host_map.items()
        if name.lower() not in base_hosts
        and not host_map.update({name.lower(): cat})
    )
    log.info("  host_to_category +%d entries", added)

    # UBERON anatomy -> unambiguous / ambiguous lists
    base_unambig_h = {t.lower() for t in base.get("unambiguous_human_terms",  [])}
    base_unambig_a = {t.lower() for t in base.get("unambiguous_animal_terms", [])}
    base_ambig     = {t.lower() for t in base.get("ambiguous_specimen_terms", [])}
    protected      = base_unambig_h | base_unambig_a | base_ambig

    new_h = [t for t in ols_terms.get("_uberon_human",     []) if t not in protected and 2 <= len(t) <= 60]
    new_a = [t for t in ols_terms.get("_uberon_animal",    []) if t not in protected and 2 <= len(t) <= 60]
    new_b = [t for t in ols_terms.get("_uberon_ambiguous", []) if t not in protected and 2 <= len(t) <= 60 and len(t.split()) <= 3]

    base.setdefault("unambiguous_human_terms",  []).extend(new_h)
    base.setdefault("unambiguous_animal_terms", []).extend(new_a)
    base.setdefault("ambiguous_specimen_terms", []).extend(new_b)
    log.info("  unambiguous_human_terms  +%d", len(new_h))
    log.info("  unambiguous_animal_terms +%d", len(new_a))
    log.info("  ambiguous_specimen_terms +%d", len(new_b))

    # UMLS synonym_map
    if umls_synonyms:
        syn_map       = base.setdefault("synonym_map", {})
        base_syn_keys = {k.lower() for k in syn_map}
        added_syns    = 0
        for canonical, synonyms in umls_synonyms.items():
            for syn in synonyms:
                s = syn.lower().strip()
                if s and s not in base_syn_keys and s != canonical.lower() and 3 <= len(s) <= 80:
                    syn_map[s] = canonical
                    base_syn_keys.add(s)
                    added_syns += 1
        log.info("  synonym_map +%d entries from UMLS", added_syns)

    # Resolve cross-category and cross-section collisions
    collision_stats = _resolve_collisions(base)

    return base, collision_stats


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def attach_metadata(data, args, ncbi_count, ols_counts, collision_stats):
    data["_metadata"] = {
        "build_date":   datetime.now(timezone.utc).isoformat(),
        "build_script": "scripts/build_dictionaries.py",
        "sources": {
            "hand_curated_base":       str(args.base),
            "ols4_api":                OLS_BASE,
            "ols4_ontologies":         list(OLS_ONTOLOGY_MAP.keys()),
            "ols4_term_counts":        ols_counts,
            "ncbi_taxonomy_source":    "local taxdmp.zip (ftp.ncbi.nlm.nih.gov/pub/taxonomy/)",
            "ncbi_host_entries_added": ncbi_count,
            "umls_api":                UMLS_BASE if args.umls_key else "skipped",
        },
        "merge_strategy":  "base_wins",
        "collision_stats": collision_stats,
        "note": (
            "Hand-curated entries always override ontology-derived entries. "
            "Terms with intra-ontology_map (distinct categories) or authoritative "
            "cross-section conflicts are recorded in ambiguous_category_terms. "
            "Same-category duplicates from overlapping OLS4 seed subtrees are "
            "deduplicated silently and do not count as collisions. "
            "ambiguous_specimen_terms is a tiebreaker and does NOT evict "
            "terms from ontology_map. "
            "Rebuild by running scripts/build_dictionaries.py."
        ),
    }
    return data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Build enriched one_health_dictionaries.json from ontology sources."
    )
    p.add_argument("--base",   default="src/biometaharmonizer/schemas/one_health_dictionaries.json")
    p.add_argument("--output", default="src/biometaharmonizer/schemas/one_health_dictionaries.json")
    p.add_argument(
        "--taxdmp",
        default=None,
        help=(
            "Path to a pre-downloaded taxdmp.zip or an extracted directory "
            "containing names.dmp and nodes.dmp. "
            "If omitted, taxdmp.zip is downloaded automatically from NCBI FTP."
        ),
    )
    p.add_argument("--umls-key",  default=None, help="UMLS API key for synonym expansion")
    p.add_argument("--skip-ols",  action="store_true")
    p.add_argument("--skip-ncbi", action="store_true")
    p.add_argument("--dry-run",   action="store_true")
    return p.parse_args()


def main():
    args      = parse_args()
    base_path = Path(args.base)

    if not base_path.exists():
        log.error("Base file not found: %s", base_path)
        sys.exit(1)

    with open(base_path, "r", encoding="utf-8") as f:
        base = json.load(f)
    log.info("Loaded base dictionary from %s", base_path)

    ols_terms  = {}
    ols_counts = {}
    if not args.skip_ols:
        ols_terms = fetch_ols_terms()
        for cat, terms in ols_terms.items():
            if not cat.startswith("_"):
                ols_counts[cat] = len(terms)
    else:
        log.info("Skipping OLS4 (--skip-ols)")

    ncbi_host_map = {}
    if not args.skip_ncbi:
        ncbi_host_map = build_host_map_from_dump(taxdmp_arg=args.taxdmp)
    else:
        log.info("Skipping NCBI Taxonomy (--skip-ncbi)")

    umls_synonyms = {}
    if args.umls_key:
        try:
            umls_synonyms = fetch_umls_synonyms(args.umls_key)
        except Exception as exc:
            log.warning("UMLS fetch failed: %s", exc)
    else:
        log.info("Skipping UMLS (no --umls-key provided)")

    enriched, collision_stats = merge_into_base(base, ols_terms, ncbi_host_map, umls_synonyms)
    attach_metadata(enriched, args, len(ncbi_host_map), ols_counts, collision_stats)

    log.info("--- Final dictionary stats ---")
    for section, val in enriched.items():
        if section == "_metadata":
            continue
        if isinstance(val, dict):
            log.info("  %-35s  %d keys",    section, len(val))
        elif isinstance(val, list):
            log.info("  %-35s  %d entries", section, len(val))

    if args.dry_run:
        log.info("Dry run - output not written.")
        return

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=4, ensure_ascii=False)
    log.info("Written to %s", out_path)


if __name__ == "__main__":
    main()
