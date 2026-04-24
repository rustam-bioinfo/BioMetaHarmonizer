#!/usr/bin/env python3
"""
build_dictionaries.py

Builds an enriched one_health_dictionaries.json by querying:
  1. OLS4 API  - ENVO, FoodOn, UBERON, Plant Ontology
  2. NCBI Taxonomy API - host_to_category for all vertebrates + plants
  3. UMLS API  - synonym expansion (optional, requires API key)

The hand-curated base file is loaded first. Any key present in the base
always wins over ontology-derived data (merge strategy: base_wins).

Usage
-----
  python scripts/build_dictionaries.py \
      --base   src/biometaharmonizer/schemas/one_health_dictionaries.json \
      --output src/biometaharmonizer/schemas/one_health_dictionaries.json \
      --umls-key YOUR_UMLS_API_KEY          # optional

Dependencies (all standard or already in requirements.txt):
  requests>=2.28

The script is intentionally standalone - no imports from biometaharmonizer.
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

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

OLS_BASE = "https://www.ebi.ac.uk/ols4/api"
NCBI_TAX_BASE = "https://api.ncbi.nlm.nih.gov/datasets/v2/taxonomy"
UMLS_BASE = "https://uts-ws.nlm.nih.gov/rest"

# OLS ontology ids -> One Health category for their terms
OLS_ONTOLOGY_MAP = {
    "envo": {
        # parent term IDs whose entire subtree maps to Environmental
        "Environmental": [
            "ENVO:00000428",   # biome
            "ENVO:00010483",   # environmental material
            "ENVO:00002297",   # environmental feature
            "ENVO:01000254",   # anthropogenic environment
        ],
    },
    "foodon": {
        "Food": [
            "FOODON:00001002",  # food product
            "FOODON:03400361",  # food material
        ],
    },
    "uberon": {
        # anatomical structures - split by whether they are human-exclusive,
        # animal-exclusive, or shared (ambiguous). We collect all and classify
        # in post-processing using taxon constraints embedded in UBERON.
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
NCBI_TAXON_ROOTS = {
    9606:  "Human",     # Homo sapiens (exact)
    40674: "Animal",    # Mammalia
    8782:  "Animal",    # Aves
    8504:  "Animal",    # Reptilia
    8292:  "Animal",    # Amphibia
    7776:  "Animal",    # Chondrichthyes (sharks/rays)
    7898:  "Animal",    # Actinopterygii (ray-finned fish)
    6656:  "Animal",    # Arthropoda
    6447:  "Animal",    # Mollusca
    33090: "Plant",     # Viridiplantae
    4751:  "Lab",       # Fungi (lab/model organisms)
}

# UBERON terms that are exclusively human-clinical context
# (no wild animal has these in practice in NCBI BioSample)
UBERON_HUMAN_EXCLUSIVE = {
    "cerebrospinal fluid", "pleural fluid", "peritoneal fluid",
    "synovial fluid", "amniotic fluid", "dialysate",
    "bronchoalveolar lavage fluid", "sputum", "dental plaque",
    "catheter", "central venous catheter",
}

# UBERON terms that are exclusively animal context in NCBI BioSample
UBERON_ANIMAL_EXCLUSIVE = {
    "rumen", "reticulum", "omasum", "abomasum",
    "gizzard", "proventriculus", "crop",
    "cloaca", "swim bladder", "gill",
    "hemolymph", "exoskeleton",
}

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})


def _get(url, params=None, retries=3, backoff=2.0):
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=20)
            if r.status_code == 429:
                wait = backoff * (attempt + 1)
                log.warning("Rate limited by %s, waiting %.0fs", url, wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            if attempt == retries - 1:
                log.error("Failed GET %s: %s", url, exc)
                return None
            time.sleep(backoff)
    return None


# ---------------------------------------------------------------------------
# OLS4 helpers
# ---------------------------------------------------------------------------

def ols_descendants(ontology, term_iri, max_terms=2000):
    """
    Fetch all descendant term labels + synonyms for a given IRI
    from OLS4. Returns list of lowercase strings.
    """
    encoded_iri = requests.utils.quote(requests.utils.quote(term_iri, safe=""))
    url = f"{OLS_BASE}/ontologies/{ontology}/terms/{encoded_iri}/descendants"
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
            if label:
                terms.append(label.lower())
            for syn in item.get("synonyms") or []:
                if syn:
                    terms.append(syn.lower())
        total_pages = data.get("page", {}).get("totalPages", 1)
        page += 1
        if page >= total_pages:
            break
        time.sleep(0.1)

    return list(dict.fromkeys(terms))  # deduplicate preserving order


def fetch_ols_terms():
    """
    Returns dict: category -> list of terms
    and separate anatomy lists for ambiguous/human/animal classification.
    """
    log.info("Fetching OLS4 terms...")
    result = {}
    anatomy_all = []

    for ontology, category_map in OLS_ONTOLOGY_MAP.items():
        for category, iris in category_map.items():
            if category == "_anatomy":
                for iri in iris:
                    log.info("  UBERON %s (anatomy)", iri)
                    terms = ols_descendants(ontology, iri, max_terms=3000)
                    anatomy_all.extend(terms)
                    log.info("    -> %d anatomy terms", len(terms))
            else:
                if category not in result:
                    result[category] = []
                for iri in iris:
                    log.info("  %s %s -> %s", ontology.upper(), iri, category)
                    terms = ols_descendants(ontology, iri)
                    result[category].extend(terms)
                    log.info("    -> %d terms", len(terms))

    # Classify anatomy terms
    human_terms = []
    animal_terms = []
    ambiguous_terms = []

    for term in anatomy_all:
        if any(excl in term for excl in UBERON_HUMAN_EXCLUSIVE):
            human_terms.append(term)
        elif any(excl in term for excl in UBERON_ANIMAL_EXCLUSIVE):
            animal_terms.append(term)
        else:
            ambiguous_terms.append(term)

    result["_uberon_human"] = human_terms
    result["_uberon_animal"] = animal_terms
    result["_uberon_ambiguous"] = ambiguous_terms

    return result


# ---------------------------------------------------------------------------
# NCBI Taxonomy helpers
# ---------------------------------------------------------------------------

def ncbi_children(taxon_id, max_results=500):
    """
    Fetch direct children of a taxon node from NCBI Taxonomy.
    Returns list of (tax_id, sci_name, common_name) tuples.
    """
    url = f"{NCBI_TAX_BASE}/taxon/{taxon_id}/children"
    data = _get(url, params={"page_size": max_results})
    if not data:
        return []
    children = []
    for taxon in data.get("taxonomy_nodes", []):
        tax = taxon.get("taxonomy", {})
        tax_id = tax.get("tax_id")
        sci_name = tax.get("organism_name", "")
        common = ""
        if tax.get("common_name"):
            common = tax["common_name"]
        if tax_id and sci_name:
            children.append((tax_id, sci_name.lower(), common.lower()))
    return children


def fetch_ncbi_host_map(depth=2):
    """
    Walk NCBI Taxonomy to depth `depth` from each root in NCBI_TAXON_ROOTS.
    Returns dict: scientific_name_lower -> category
                  common_name_lower     -> category  (where available)
    """
    log.info("Fetching NCBI Taxonomy host mappings (depth=%d)...", depth)
    host_map = {}

    for root_id, category in NCBI_TAXON_ROOTS.items():
        if root_id == 9606:
            host_map["homo sapiens"] = "Human"
            host_map["human"] = "Human"
            continue

        queue = [(root_id, 0)]
        visited = set()

        while queue:
            taxon_id, current_depth = queue.pop(0)
            if taxon_id in visited:
                continue
            visited.add(taxon_id)

            children = ncbi_children(taxon_id)
            for child_id, sci_name, common_name in children:
                host_map[sci_name] = category
                if common_name:
                    host_map[common_name] = category
                if current_depth + 1 < depth:
                    queue.append((child_id, current_depth + 1))

            time.sleep(0.05)

        log.info("  taxon %d (%s): %d names collected so far",
                 root_id, category, len(host_map))

    return host_map


# ---------------------------------------------------------------------------
# UMLS helpers (optional)
# ---------------------------------------------------------------------------

def umls_get_ticket(api_key):
    tgt_url = "https://utslogin.nlm.nih.gov/cas/v1/api-key"
    r = requests.post(tgt_url, data={"apikey": api_key}, timeout=15)
    r.raise_for_status()
    import re
    match = re.search(r'action="(.*?)"', r.text)
    if not match:
        raise RuntimeError("Could not parse UMLS TGT from response")
    return match.group(1)


def umls_service_ticket(tgt_url):
    r = requests.post(tgt_url, data={"service": "http://umlsks.nlm.nih.gov"}, timeout=15)
    r.raise_for_status()
    return r.text.strip()


def umls_synonyms_for_concept(cui, tgt_url):
    """
    Fetch all English atom strings for a UMLS CUI.
    Returns list of lowercase strings.
    """
    st = umls_service_ticket(tgt_url)
    url = f"{UMLS_BASE}/content/current/CUI/{cui}/atoms"
    data = _get(url, params={"ticket": st, "language": "ENG", "pageSize": 100})
    if not data:
        return []
    synonyms = []
    for atom in data.get("result", []):
        name = atom.get("name", "")
        if name:
            synonyms.append(name.lower())
    return synonyms


# Seed CUIs for specimen types we care about most
UMLS_SPECIMEN_CUIS = {
    # CUI       : canonical term
    "C0005767":  "blood",
    "C0042036":  "urine",
    "C0038569":  "sputum",
    "C0007555":  "cerebrospinal fluid",
    "C0205189":  "pleural fluid",
    "C0003967":  "ascitic fluid",
    "C0039981":  "synovial fluid",
    "C0006252":  "bronchial lavage",
    "C0444941":  "wound",
    "C0000735":  "abscess",
    "C0032227":  "pus",
    "C0015411":  "feces",
    "C0521481":  "rectal swab",
    "C0029001":  "oral swab",
    "C0042048":  "vaginal swab",
    "C0877612":  "nasal swab",
    "C0586478":  "throat swab",
    "C0042036":  "urine",
    "C0005767":  "blood",
}


def fetch_umls_synonyms(api_key):
    """
    Returns dict: canonical_term -> list of synonym strings
    """
    log.info("Fetching UMLS synonyms...")
    tgt_url = umls_get_ticket(api_key)
    synonym_map = {}
    for cui, canonical in UMLS_SPECIMEN_CUIS.items():
        syns = umls_synonyms_for_concept(cui, tgt_url)
        if syns:
            synonym_map[canonical] = syns
            log.info("  %s (%s): %d synonyms", canonical, cui, len(syns))
        time.sleep(0.1)
    return synonym_map


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

def merge_into_base(base, ols_terms, ncbi_host_map, umls_synonyms):
    """
    Merge ontology-derived data into base dictionary.
    Base always wins on conflict.

    Strategy per section:
      ontology_map  : extend lists with new terms not already present
      host_to_category : add entries not already in base
      synonym_map   : add synonym -> canonical pairs not already in base
      unambiguous_human_terms  : extend with UBERON human-exclusive terms
      unambiguous_animal_terms : extend with UBERON animal-exclusive terms
      ambiguous_specimen_terms : extend with UBERON ambiguous anatomy terms
                                 that are short (<= 3 words) and not already
                                 in unambiguous sets
    """
    log.info("Merging ontology data into base dictionary...")

    # --- ontology_map ---
    ont_map = base.setdefault("ontology_map", {})
    for category in ("Environmental", "Food", "Plant"):
        existing = set(ont_map.get(category, []))
        new_terms = [
            t for t in ols_terms.get(category, [])
            if t not in existing and len(t) >= 3
        ]
        if new_terms:
            ont_map.setdefault(category, []).extend(new_terms)
            log.info("  ontology_map[%s] +%d terms", category, len(new_terms))

    # --- host_to_category ---
    host_map = base.setdefault("host_to_category", {})
    base_hosts = {k.lower() for k in host_map}
    added_hosts = 0
    for name, category in ncbi_host_map.items():
        if name.lower() not in base_hosts:
            host_map[name.lower()] = category
            added_hosts += 1
    log.info("  host_to_category +%d entries", added_hosts)

    # --- unambiguous human/animal from UBERON ---
    base_unambig_human = set(t.lower() for t in base.get("unambiguous_human_terms", []))
    base_unambig_animal = set(t.lower() for t in base.get("unambiguous_animal_terms", []))
    base_ambig = set(t.lower() for t in base.get("ambiguous_specimen_terms", []))
    all_protected = base_unambig_human | base_unambig_animal | base_ambig

    new_human = [
        t for t in ols_terms.get("_uberon_human", [])
        if t not in all_protected and 2 <= len(t) <= 60
    ]
    new_animal = [
        t for t in ols_terms.get("_uberon_animal", [])
        if t not in all_protected and 2 <= len(t) <= 60
    ]
    new_ambig = [
        t for t in ols_terms.get("_uberon_ambiguous", [])
        if t not in all_protected
        and 2 <= len(t) <= 60
        and len(t.split()) <= 3
    ]

    base.setdefault("unambiguous_human_terms", []).extend(new_human)
    base.setdefault("unambiguous_animal_terms", []).extend(new_animal)
    base.setdefault("ambiguous_specimen_terms", []).extend(new_ambig)
    log.info("  unambiguous_human_terms +%d", len(new_human))
    log.info("  unambiguous_animal_terms +%d", len(new_animal))
    log.info("  ambiguous_specimen_terms +%d", len(new_ambig))

    # --- synonym_map from UMLS ---
    if umls_synonyms:
        syn_map = base.setdefault("synonym_map", {})
        base_syn_keys = {k.lower() for k in syn_map}
        added_syns = 0
        for canonical, synonyms in umls_synonyms.items():
            for syn in synonyms:
                syn_lower = syn.lower().strip()
                if (
                    syn_lower not in base_syn_keys
                    and syn_lower != canonical.lower()
                    and 3 <= len(syn_lower) <= 80
                ):
                    syn_map[syn_lower] = canonical
                    base_syn_keys.add(syn_lower)
                    added_syns += 1
        log.info("  synonym_map +%d entries from UMLS", added_syns)

    return base


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def attach_metadata(data, args, ncbi_count, ols_counts):
    data["_metadata"] = {
        "build_date": datetime.now(timezone.utc).isoformat(),
        "build_script": "scripts/build_dictionaries.py",
        "sources": {
            "hand_curated_base": str(args.base),
            "ols4_api": OLS_BASE,
            "ols4_ontologies": list(OLS_ONTOLOGY_MAP.keys()),
            "ols4_term_counts": ols_counts,
            "ncbi_taxonomy_api": NCBI_TAX_BASE,
            "ncbi_host_entries_added": ncbi_count,
            "umls_api": UMLS_BASE if args.umls_key else "skipped (no key provided)",
        },
        "merge_strategy": "base_wins",
        "note": (
            "Hand-curated entries always override ontology-derived entries. "
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
    p.add_argument(
        "--base",
        default="src/biometaharmonizer/schemas/one_health_dictionaries.json",
        help="Path to hand-curated base JSON (default: %(default)s)",
    )
    p.add_argument(
        "--output",
        default="src/biometaharmonizer/schemas/one_health_dictionaries.json",
        help="Output path (default: overwrites base)",
    )
    p.add_argument(
        "--ncbi-depth",
        type=int,
        default=2,
        help="Taxonomy walk depth from each NCBI root (default: 2)",
    )
    p.add_argument(
        "--umls-key",
        default=None,
        help="UMLS API key for synonym expansion (optional)",
    )
    p.add_argument(
        "--skip-ols",
        action="store_true",
        help="Skip OLS4 queries (faster, for testing NCBI/UMLS only)",
    )
    p.add_argument(
        "--skip-ncbi",
        action="store_true",
        help="Skip NCBI Taxonomy queries",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print stats but do not write output file",
    )
    return p.parse_args()


def main():
    args = parse_args()

    base_path = Path(args.base)
    if not base_path.exists():
        log.error("Base file not found: %s", base_path)
        sys.exit(1)

    with open(base_path, "r", encoding="utf-8") as f:
        base = json.load(f)
    log.info("Loaded base dictionary from %s", base_path)

    # --- OLS4 ---
    ols_terms = {}
    ols_counts = {}
    if not args.skip_ols:
        ols_terms = fetch_ols_terms()
        for cat, terms in ols_terms.items():
            if not cat.startswith("_"):
                ols_counts[cat] = len(terms)
                log.info("OLS terms collected: %s -> %d", cat, len(terms))
    else:
        log.info("Skipping OLS4 (--skip-ols)")

    # --- NCBI Taxonomy ---
    ncbi_host_map = {}
    if not args.skip_ncbi:
        ncbi_host_map = fetch_ncbi_host_map(depth=args.ncbi_depth)
        log.info("NCBI host map: %d entries", len(ncbi_host_map))
    else:
        log.info("Skipping NCBI Taxonomy (--skip-ncbi)")

    # --- UMLS ---
    umls_synonyms = {}
    if args.umls_key:
        try:
            umls_synonyms = fetch_umls_synonyms(args.umls_key)
        except Exception as exc:
            log.warning("UMLS fetch failed: %s", exc)
    else:
        log.info("Skipping UMLS (no --umls-key provided)")

    # --- Merge ---
    enriched = merge_into_base(base, ols_terms, ncbi_host_map, umls_synonyms)

    # Track how many new hosts were added
    ncbi_new = len(ncbi_host_map)

    attach_metadata(enriched, args, ncbi_new, ols_counts)

    # --- Stats ---
    log.info("--- Final dictionary stats ---")
    for section, val in enriched.items():
        if section == "_metadata":
            continue
        if isinstance(val, dict):
            log.info("  %-35s  %d keys", section, len(val))
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
