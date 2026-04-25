# Dictionary Build Pipeline

**Last updated:** 2026-04-25
**Script:** `scripts/build_dictionaries.py`

## Purpose

`one_health_dictionaries.json` is the compiled classification dictionary used by `one_health.py` at runtime.
It is built once from three external sources and committed to the repository.
The build script must be re-run whenever ontology content needs refreshing.

## Sources

| Source | What it provides | API / file |
|---|---|---|
| Hand-curated base | Starting point; always wins on collision | `one_health_dictionaries.json` itself |
| OLS4 (EBI) | Environmental, Food, Plant, Anatomy terms | `https://www.ebi.ac.uk/ols4/api` |
| NCBI Taxonomy local dump | Host species names → Animal / Plant / Human | `ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdmp.zip` |
| UMLS (optional) | Specimen synonym expansion | `https://uts-ws.nlm.nih.gov/rest` |

## Dictionary structure

```json
{
    "ontology_map": {
        "Environmental": ["soil", "biome", "sediment", ...],
        "Food":          ["food product", "meat", ...],
        "Plant":         ["root", "leaf", "stem", ...]
    },
    "host_to_category": {
        "sus scrofa": "Animal",
        "homo sapiens": "Human",
        "oryza sativa": "Plant",
        ...
    },
    "unambiguous_human_terms":  ["sputum", "cerebrospinal fluid", ...],
    "unambiguous_animal_terms": ["rumen", "gizzard", ...],
    "ambiguous_specimen_terms": ["blood", "serum", "feces", ...],
    "ambiguous_category_terms": {"blood": ["Food", "Animal"], ...},
    "synonym_map":              {"csf": "cerebrospinal fluid", ...},
    "_metadata": { ... }
}
```

## OLS4 section

The script queries the [OLS4 REST API](https://www.ebi.ac.uk/ols4/api) using `hierarchicalDescendants` pagination
to collect all subclass terms under seed IRIs defined in `OLS_ONTOLOGY_MAP`.

### Ontologies and seed terms

| Ontology | Category | Seed IRI | Label |
|---|---|---|---|
| ENVO | Environmental | ENVO:00000428 | biome |
| ENVO | Environmental | ENVO:00010483 | environmental material |
| ENVO | Environmental | ENVO:01000254 | anthropogenic environment |
| ENVO | Environmental | ENVO:01001110 | ecosystem |
| ENVO | Environmental | ENVO:00000063 | soil |
| ENVO | Environmental | ENVO:00000015 | ocean |
| ENVO | Environmental | ENVO:00000873 | freshwater body |
| ENVO | Environmental | ENVO:00000134 | sediment |
| ENVO | Environmental | ENVO:00002006 | water |
| FoodOn | Food | FOODON:00001002 | food product |
| FoodOn | Food | FOODON:03400361 | food material |
| FoodOn | Food | FOODON:00001709 | animal food product |
| FoodOn | Food | FOODON:03420194 | plant food product |
| UBERON | _(anatomy)_ | UBERON:0000465 | material anatomical entity |
| PO | Plant | PO:0025131 | plant anatomical entity |

### Synonym scope policy

Only `hasExactSynonym` / `has_exact_synonym` are collected.
`hasBroadSynonym`, `hasNarrowSynonym`, and `hasRelatedSynonym` are intentionally excluded:

- **broad** — the synonym is a supercategory (e.g. `"animal product"` as a broad synonym for `"beef"`) — too vague for sample assignment.
- **narrow** — more specific than the labelled concept; creates noise at category edges.
- **related** — loose association only; high false-positive risk against free-text metadata.

### Term cleaning (`_clean_ols_term`)

Every collected string passes through a cleaning pipeline before storage:

1. Non-ASCII characters → discard (not matchable against BioSample free text)
2. Language-tagged synonyms `"...(spanish, exact)"` → discard
3. Bare OBO scope tag leaks `"...(exact)"` → strip suffix
4. GS1/GPC product codes `"10000215 - ice cream (gs1 gpc)"` → discard
5. String ≤ 2 characters after cleaning → discard

## NCBI Taxonomy section

### Why a local dump instead of Entrez API

The previous implementation called `esearch txid[Subtree]` + `efetch` per clade.
This had three critical limitations:

| Problem | Impact |
|---|---|
| `retmax=10000` hard cap in esearch | Large clades (Arthropoda ~400k spp., Mammalia ~100k spp.) were silently truncated |
| Hundreds of HTTP round-trips | 10–15 minutes per build run; fails on rate limits or network issues |
| efetch XML returns one common name per taxon | Missed common names, genbank common names, equivalent names, and all subspecies strings |

The current implementation downloads `taxdmp.zip` (~65 MB) once and does the entire subtree walk in memory using pandas + a BFS over a parent→children dict.
No API calls, no rate limits, no truncation.

### Algorithm

```
1. Download taxdmp.zip from ftp.ncbi.nlm.nih.gov/pub/taxonomy/ (or use --taxdmp)
2. Extract names.dmp + nodes.dmp to a temp dir
3. Parse names.dmp with pandas:
       sep='\t|\t', keep columns: tax_id, name_txt, name_class
       filter name_class to NAMES_DMP_KEEP_CLASSES
       lowercase name_txt
4. Parse nodes.dmp with pandas:
       sep='\t|\t', keep columns: tax_id, parent_tax_id
       build defaultdict(list): parent_id -> [child_ids]
5. For each root in NCBI_TAXON_ROOTS (except 9606 Human):
       BFS from root_id over children dict -> frozenset of all descendant tax_ids
       filter names df to those tax_ids
       emit name_txt -> category
6. Add hard-coded Human entries (homo sapiens, human, man)
7. Clean up temp dir
```

### Name classes kept

| `name_class` in names.dmp | Reason |
|---|---|
| `scientific name` | Primary identifier, always present, one per taxon |
| `common name` | Plain English names: `domestic cat`, `chicken` |
| `genbank common name` | What NCBI itself uses in metadata: `cow`, `silkworm` |
| `equivalent name` | Accepted synonyms, e.g. subspecies alternatives |

Dropped: `synonym`, `authority`, `blast name`, `in-part`, `includes`, `type material`, `anamorph`, `teleomorph`.

### Taxon roots

| tax_id | Clade | Category |
|---|---|---|
| 9606 | Homo sapiens | Human (exact match only) |
| 40674 | Mammalia | Animal |
| 8782 | Aves | Animal |
| 8504 | Reptilia | Animal |
| 8292 | Amphibia | Animal |
| 7776 | Chondrichthyes | Animal |
| 7898 | Actinopterygii | Animal |
| 6656 | Arthropoda | Animal |
| 6447 | Mollusca | Animal |
| 33090 | Viridiplantae | Plant |

Fungi (txid4751) are intentionally excluded — their One Health category is context-dependent and cannot be resolved from taxonomy alone.

## Merge strategy

The script loads the hand-curated base JSON first.
**Any key already present in the base always wins** over ontology-derived data.
New terms are appended, never replaced.

After merging, `_resolve_collisions()` detects terms that appear in multiple categories or conflict across dictionary sections, removes them from `ontology_map`, and records them in `ambiguous_category_terms` with the list of conflicting sources.

## Usage

```bash
# Full rebuild (auto-downloads taxdmp.zip ~65 MB)
python scripts/build_dictionaries.py \
    --base   src/biometaharmonizer/schemas/one_health_dictionaries.json \
    --output src/biometaharmonizer/schemas/one_health_dictionaries.json

# Use a pre-downloaded taxdmp.zip
python scripts/build_dictionaries.py --taxdmp /data/taxdmp.zip

# Use an already-extracted directory
python scripts/build_dictionaries.py --taxdmp /data/taxonomy/

# Skip NCBI taxonomy (OLS4 only)
python scripts/build_dictionaries.py --skip-ncbi

# Skip OLS4 (taxonomy only)
python scripts/build_dictionaries.py --skip-ols

# With UMLS synonym expansion
python scripts/build_dictionaries.py --umls-key YOUR_KEY

# Dry run — validate without writing
python scripts/build_dictionaries.py --dry-run
```

## Dependencies

```
requests>=2.28
pandas>=1.5
```

Both are listed in `requirements.txt`. No other build-time dependencies are needed.

## Collision resolution

After all sources are merged, the script runs a two-pass collision check:

1. **Intra-`ontology_map`** — same term string in two or more category lists (e.g. `"blood"` in both Food and Animal). The term is removed from all category lists and added to `ambiguous_category_terms`.

2. **Cross-section** — a term in `ontology_map` also appears in `host_to_category`, `unambiguous_human_terms`, `unambiguous_animal_terms`, or `ambiguous_specimen_terms`. Same resolution: removed from `ontology_map`, recorded in `ambiguous_category_terms`.

Terms already present in the hand-curated base are exempt — if a human already decided the category, there is no ambiguity.

## Changelog

| Date | Change |
|---|---|
| 2026-04-25 | Replace Entrez esearch/efetch subtree walk with local taxdmp.zip parsing. Add `--taxdmp` CLI argument. Drop `--ncbi-email` and `--ncbi-key`. Remove 10k truncation limit. Add `nodes.dmp` BFS subtree walk. Keep only exact OLS synonyms (drop broad/narrow/related). |
| prior | Initial implementation using Entrez eutils API for host taxonomy. |
