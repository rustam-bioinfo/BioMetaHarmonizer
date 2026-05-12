# BioMetaHarmonizer

[![version](https://img.shields.io/badge/version-1.0.0-blue)](#)
[![python](https://img.shields.io/badge/python-3.9%2B-blue)](#)
[![license](https://img.shields.io/badge/license-MIT-green)](#)
[![status](https://img.shields.io/badge/status-stable-brightgreen)](#)
[![PyPI](https://img.shields.io/pypi/v/biometaharmonizer)](https://pypi.org/project/biometaharmonizer/)
[![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://rustam-bioinfo.github.io/BioMetaHarmonizer/)

A Python package for fetching, parsing, and standardizing NCBI BioSample metadata for large-scale genomic epidemiology.

---

## What it does

NCBI BioSample metadata is free-text, crowd-sourced, and inconsistent across submitters. BioMetaHarmonizer fetches BioSample XML records via the Entrez API, maps raw attribute names to a fixed set of standard columns, normalizes placeholder null values, parses dates and geographic strings, and assigns One Health categories. The result is a pandas DataFrame that can be written to CSV, TSV, Excel, Parquet, or JSON Lines (JSONL) — including multiple formats in a single run.

Input can be BioSample accessions (`SAMN`, `SAME`, `SAMD`), assembly accessions (`GCF_`, `GCA_`), or a mix of both. Assembly accessions are resolved to BioSample IDs through locally cached NCBI assembly summary flat files.

Records submitted under NCBI pathogen packages (e.g. `Pathogen.cl.1.0`, `Pathogen.env.1.0`) often carry a structured `<Antibiogram>` section alongside standard attributes. BioMetaHarmonizer parses the antibiogram and serializes it as a compact JSON list in `_extra_attributes["antibiogram"]` so that MIC and phenotype data are never silently discarded.

---

## Installation

Install from PyPI:

```bash
pip install biometaharmonizer
```

Requires Python 3.9+. Dependencies are declared in `pyproject.toml` and installed automatically.

**Development install** (editable, from source):

```bash
git clone https://github.com/rustam-bioinfo/BioMetaHarmonizer.git
cd BioMetaHarmonizer
pip install -e .
```

The package ships with a minimal hand-curated `one_health_dictionaries.json`. For complete One Health classification — in particular, full NCBI taxonomy coverage of host species names — rebuild this file before use. See [Rebuilding schema files](#rebuilding-schema-files).

---

## Quick start

### Command line

```bash
# Single output file (format inferred from extension)
biometaharmonizer run \
    --input  accessions.txt \
    --email  your@email.com \
    --output harmonized.csv

# Save to multiple formats in one run
biometaharmonizer run \
    --input  accessions.txt \
    --email  your@email.com \
    --output harmonized.csv \
    --format csv tsv excel
# Produces: harmonized.csv, harmonized.tsv, harmonized.xlsx
```

| Flag | Default | Description |
|---|---|---|
| `--input FILE` | required | Path to accession list (one per line) |
| `--email EMAIL` | required | Valid contact email for NCBI Entrez — must contain `@` and a domain |
| `--output FILE` | required | Output file path (used as the base name for multi-format output) |
| `--api-key KEY` | — | NCBI API key; raises rate limit from 3 to 10 requests/second |
| `--cache-dir DIR` | `~/.biometaharmonizer/cache/` | Directory for assembly summary flat files |
| `--format FORMAT [FORMAT ...]` | inferred from file extension | One or more of: `csv`, `tsv`, `excel`, `parquet`, `jsonl`. When multiple formats are given the stem of `--output` is reused and the correct extension is substituted for each format. Omit to infer from the output file extension. |
| `--summary FILE` | — | Write a per-column fill-rate CSV |
| `--fetch-batch-size N` | `200` | Number of records per efetch request |
| `--esearch-batch-size N` | `200` | Number of accessions per esearch term |
| `--refresh-cache` | off | Force re-download of assembly summary flat files regardless of age |
| `--verbose` | off | Enable DEBUG-level logging |

---
## Output columns

The output DataFrame contains **57 columns**. Columns with no data for a given dataset are present and filled with `NaN`. Attributes that do not map to any column are preserved as a JSON string in `_extra_attributes`.


| # | Column | Source | Description |
|---|--------|--------|-------------|
| 1 | `biosample_accession` | BioSample XML | NCBI BioSample accession (e.g. `SAMN07597573`) |
| 2 | `biosample_id` | BioSample XML | NCBI internal numeric BioSample ID |
| 3 | `sra_accession` | BioSample XML | Linked SRA accession, if present |
| 4 | `bioproject_accession` | BioSample XML / assembly index | Parent BioProject accession |
| 5 | `assembly_accession_refseq` | Assembly index | RefSeq assembly accession (GCF\_) |
| 6 | `assembly_accession_genbank` | Assembly index | GenBank assembly accession (GCA\_) |
| 7 | `sample_name_id` | BioSample XML | Submitter sample name from `<Id db_label="Sample name">` |
| 8 | `taxonomy_id` | BioSample XML | NCBI Taxonomy numeric ID |
| 9 | `taxonomy_name` | BioSample XML | Taxon name for the assigned taxonomy_id |
| 10 | `organism_name` | BioSample XML | Organism name from `<OrganismName>`; falls back to taxonomy_name |
| 11 | `collection_date` | BioSample attribute → DateEngine | Collection date normalized to ISO 8601 |
| 12 | `collection_date_range` | DateEngine | Verbatim original string for range/approximate date inputs; NaN for point dates |
| 13 | `geo_loc_name` | BioSample attribute | Raw geographic location string as submitted |
| 14 | `lat_lon` | BioSample attribute | Decimal lat/lon as submitted |
| 15 | `geo_country` | GeoEngine | Country resolved from `geo_loc_name` |
| 16 | `geo_region` | GeoEngine | Sub-national region; populated only from colon-format inputs (`"Country: Region, Locality"`); `NaN` for comma-only inputs |
| 17 | `geo_locality` | GeoEngine | Locality after the region in colon format, or the part after the first comma in comma-only inputs |
| 18 | `geo_iso3166` | GeoEngine | ISO 3166-1 alpha-2 country code; historical names tagged `HISTORICAL` |
| 19 | `geo_sea_ocean` | GeoEngine | Named aquatic feature from `geo_loc_name` — covers oceans, seas, gulfs, bays, straits, fjords, lakes, reservoirs, and other water bodies |
| 20 | `host` | BioSample attribute | Host organism name |
| 21 | `host_disease` | BioSample attribute | Disease associated with host at sampling |
| 22 | `host_age` | BioSample attribute | Age of host |
| 23 | `host_sex` | BioSample attribute | Biological sex of host |
| 24 | `host_tissue_sampled` | BioSample attribute | Tissue or body site sampled |
| 25 | `isolation_source` | BioSample attribute | Material or environment from which the isolate was obtained |
| 26 | `sample_type` | BioSample attribute | Sample type or specimen classification |
| 27 | `env_broad_scale` | BioSample attribute | Broad environmental context (ENVO) |
| 28 | `env_local_scale` | BioSample attribute | Local environmental feature (ENVO) |
| 29 | `env_medium` | BioSample attribute | Environmental medium (ENVO) |
| 30 | `one_health_category` | OneHealthClassifier | One of: `Human`, `Animal`, `Plant`, `Food`, `Environmental`, `Unclassified` |
| 31 | `one_health_term` | OneHealthClassifier | The specific term or phrase that triggered the classification |
| 32 | `one_health_confidence` | OneHealthClassifier | Float in [0, 1] — see [One Health classification](#one-health-classification) |
| 33 | `one_health_evidence_level` | OneHealthClassifier | Discretized confidence: `high` (≥0.85), `medium` (≥0.60), `low` (≥0.30), `unresolved` |
| 34 | `one_health_processing` | OneHealthClassifier | Processing/handling term detected in the field text (e.g. `pasteurized`, `frozen`), if any |
| 35 | `one_health_setting` | OneHealthClassifier | Setting term detected in the field text (e.g. `clinical`, `farm`, `retail`) |
| 36 | `one_health_source_field` | OneHealthClassifier | Which input field produced the winning classification |
| 37 | `isolate` | BioSample attribute | Isolate identifier |
| 38 | `strain` | BioSample attribute | Strain designation |
| 39 | `sub_strain` | BioSample attribute | Sub-strain designation |
| 40 | `serotype` | BioSample attribute | Serotype |
| 41 | `serovar` | BioSample attribute | Serovar |
| 42 | `genotype` | BioSample attribute | Genotype or sequence type |
| 43 | `culture_collection` | BioSample attribute | Culture collection identifier |
| 44 | `outbreak` | BioSample attribute | Outbreak identifier |
| 45 | `sequencing_method` | BioSample attribute | Sequencing platform |
| 46 | `assembly_method` | BioSample attribute | Genome assembly software |
| 47 | `collected_by` | BioSample attribute; `<Owner/Name>` fallback | Collector name or institution |
| 48 | `ncbi_package` | BioSample XML | NCBI BioSample package (e.g. `Microbe.1.0`) |
| 49 | `submission_date` | BioSample XML | Date first submitted |
| 50 | `last_update` | BioSample XML | Date last modified |
| 51 | `publication_date` | BioSample XML | Date made publicly available |
| 52 | `access` | BioSample XML | `public` or `controlled-access` |
| 53 | `status` | BioSample XML | Record status (e.g. `live`, `suppressed`) |
| 54 | `status_date` | BioSample XML | Date current status was assigned |
| 55 | `title` | BioSample XML | Free-text title of the BioSample record |
| 56 | `description_comment` | BioSample XML | Free-text description or comment block |
| 57 | `_extra_attributes` | JSON | All attributes that could not be mapped to a schema column, serialized as a JSON dict. Also contains `submission_owner` and `submission_contact` when `<Owner>` provenance is present alongside an explicit collector. For records submitted under pathogen packages, contains an `antibiogram` key (see [Antibiogram data](#antibiogram-data)). |

---

## Antibiogram data

BioSample records submitted under NCBI pathogen packages (`Pathogen.cl.1.0`, `Pathogen.env.1.0`, etc.) may include a structured `<Antibiogram>` section that is a sibling of `<Attributes>` in the XML — not a child. Standard attribute parsers that only iterate `<Attributes>` silently drop this section. BioMetaHarmonizer parses it explicitly.

When an antibiogram is present, `_extra_attributes["antibiogram"]` contains a compact JSON-encoded list of dicts, one per antibiotic row. Each dict includes whichever of the following fields NCBI populated for that row:

| Field | Description |
|---|---|
| `antibiotic_name` | Antibiotic name (e.g. `amikacin`) |
| `resistance_phenotype` | `susceptible`, `resistant`, or `intermediate` |
| `measurement_sign` | `==`, `<=`, `>=`, `<`, `>` |
| `measurement` | Numeric MIC or disk diffusion value |
| `measurement_units` | `mg/L`, `mm`, etc. |
| `laboratory_typing_method` | `MIC`, `disk diffusion`, etc. |
| `laboratory_typing_platform` | Instrument or method platform |
| `vendor` | Reagent/kit vendor |
| `laboratory_typing_method_version_or_reagent` | Version or reagent identifier |
| `testing_standard` | `CLSI`, `EUCAST`, etc. |

Fields with null or missing values are omitted from each row dict so the JSON payload stays compact. Rows where all fields resolved to null are excluded entirely.

**Extracting antibiogram data from a result DataFrame:**

```python
import json
import pandas as pd

def extract_antibiogram(df):
    rows = []
    for _, rec in df.iterrows():
        extras = rec.get("_extra_attributes")
        if not extras:
            continue
        try:
            d = json.loads(extras)
        except (ValueError, TypeError):
            continue
        ab = d.get("antibiogram")
        if not ab:
            continue
        ab_rows = json.loads(ab) if isinstance(ab, str) else ab
        for row in ab_rows:
            row["biosample_accession"] = rec["biosample_accession"]
            rows.append(row)
    return pd.DataFrame(rows)

antibiogram_df = extract_antibiogram(df)
```

---

## Attribute resolution order

For each `<Attribute>` element in BioSample XML, the column mapping is resolved in this order:

1. **`harmonized_name` direct match** — if the NCBI-assigned `harmonized_name` matches a schema column exactly, it is used without any synonym lookup.
2. **Synonym lookup on `harmonized_name`** — if not a direct match, the `harmonized_name` is looked up in the synonym table. If the resolved key is in the schema, it is used; otherwise the resolved key is stored in `_extra_attributes`.
3. **Synonym lookup on `attribute_name`** — if `harmonized_name` is absent or unresolvable, the raw `attribute_name` is tried.
4. **`_extra_attributes`** — any attribute that could not be resolved by any of the above is written to `_extra_attributes` as a JSON key-value pair.

The synonym table is built from two layers in `synonyms.py` and cached for the lifetime of the process:

- **Layer 1 — `schemas/unified.json`** — manually curated synonym lists for all standard keys.
- **Layer 2 — `schemas/ncbi_attributes.xml`** — the official NCBI BioSample harmonization table. Optional; loaded only if present.

Both `ingestion.py` and `key_mapper.py` use the same `build_synonym_lookup()` function.

---

## Null normalization

During XML parsing, placeholder values are converted to `None` before any downstream processing. The full pattern list covers:

- `missing`, `missing: lab stock`, `missing: data agreement established pre-2023`
- `N/A`, `na`, `null`, `none`, `nil`, `-`, `.`
- `unknown`, `not provided`, `not collected`, `not applicable`, `not available`, `not determined`, `not recorded`, `not reported`
- `unavailable`, `unspecified`, `undetermined`, `unidentified`
- `restricted`, `restricted access`, `withheld`, `confidential`
- `tbd`, `tba`

Common misspellings (`misssing`, `unkown`, `unknwon`) are also matched. Matching is case-insensitive.

---

## Assembly summary cache

On the first run, `ingest()` downloads two NCBI flat files to resolve assembly accessions and BioProject links:

- `assembly_summary_refseq.txt` (~100–300 MB)
- `assembly_summary_genbank.txt` (~100–300 MB)

These are cached in `~/.biometaharmonizer/cache/` (overridable with `--cache-dir` or `set_cache_dir()`). Files older than 7 days are automatically deleted and re-downloaded on the next run.

To force a refresh before the 7-day TTL expires — for example, immediately after a large batch of new assemblies is added to NCBI — pass `refresh_cache=True` to `ingest()` or use `--refresh-cache` on the CLI:

```bash
biometaharmonizer run --input ids.txt --email you@example.com \
    --output out.csv --refresh-cache
```

```python
df = ingest("ids.txt", email="you@example.com", refresh_cache=True)
```

---

## Entrez rate limits

Without an API key, NCBI allows 3 requests per second. With a key, the limit is 10 requests per second. BioMetaHarmonizer enforces inter-request sleep intervals automatically based on whether an API key is set.

Register a free API key at https://www.ncbi.nlm.nih.gov/account/ and pass it as:

```bash
biometaharmonizer run --input ids.txt --email you@example.com \
    --api-key YOUR_KEY --output out.csv
```

or:

```python
df = ingest("ids.txt", email="you@example.com", api_key="YOUR_KEY")
```

---

## Geospatial parsing

`GeoEngine` splits `geo_loc_name` into five structured columns: `geo_country`, `geo_region`, `geo_locality`, `geo_iso3166`, and `geo_sea_ocean`.

The parser recognizes two input formats:

- **Colon format** `"Country: Region, Locality"` — the part before `:` becomes `geo_country`, the first segment after `:` becomes `geo_region`, and any remainder after the comma becomes `geo_locality`.
- **Comma-only format** `"Country, Locality"` — the part before the first `,` becomes `geo_country` and the remainder becomes `geo_locality`. `geo_region` is left `NaN`.

Parenthetical qualifiers (e.g. `"United Kingdom (England, Wales & N. Ireland)"`, `"Pacific Ocean (NE)"`, `"Russia (European part)"`) are stripped from the country token before any lookup. This means both country names and water body names with parenthetical qualifiers are correctly resolved — countries are not missed by pycountry and water bodies are not misrouted to the country resolver.

Water body detection (`geo_sea_ocean`) uses a two-tier lookup. Tier 1 is an explicit set of canonical names that covers all major oceans, seas, gulfs, bays, and straits — matched with an exact case-insensitive lookup for correctness and explicitness. Names in this set are consciously verified, and entries like `"English Channel"` and `"Mozambique Channel"` bypass the regex false-positive risk entirely because they are resolved before Tier 2 runs. Tier 2 is a regex fallback that catches any token containing a water-body keyword (`ocean`, `sea`, `gulf`, `bay`, `strait`, `fjord`, `bight`, `sound`, `inlet`, `lagoon`, `lake`, `reservoir`, `estuary`, `delta`, `reef`, `atoll`) not already in Tier 1. A negative lookahead blocks known false positives: `"Channel Islands"`, `"Gulf States"`, `"British Indian Ocean Territory"`, and similar compound names where the keyword is part of a political entity name rather than a water body.

Coordinate data (e.g. `"40.71 N, 74.00 W"`) belongs in the `lat_lon` attribute, not `geo_loc_name`. Strings submitted to `geo_loc_name` that look like coordinates are treated as unparseable and return all-NaN geo columns.

| Input | Result |
|---|---|
| `"USA: California, Los Angeles"` | country=USA, region=California, locality=Los Angeles, iso=US |
| `"USA: California"` | country=USA, region=California, iso=US |
| `"Germany, Bavaria"` | country=Germany, locality=Bavaria, iso=DE |
| `"France"` | country=France, iso=FR |
| `"Pacific Ocean"` | sea\_ocean=Pacific Ocean |
| `"Pacific Ocean (NE)"` | sea\_ocean=Pacific Ocean |
| `"Pacific Ocean: Mariana Trench"` | sea\_ocean=Pacific Ocean, locality=Mariana Trench |
| `"Red Sea (sampling site 3): surface"` | sea\_ocean=Red Sea, locality=surface |
| `"Gaza Strip"` | country=Gaza Strip, iso=PS |
| `"West Bank"` | country=West Bank, iso=PS |
| `"United Kingdom (England, Wales & N. Ireland)"` | country=United Kingdom, iso=GB |
| `"not applicable"` | all geo columns NaN |
| `"Lake Baikal"` | sea\_ocean=Lake Baikal |
| `"Gulf of Bothnia"` | sea\_ocean=Gulf of Bothnia |
| `"Svalbard: Revvatnet basin, southern Spitsbergen"` | country=Svalbard, region=Revvatnet basin, locality=southern Spitsbergen, iso=SJ |
| `"Kosovo"` | country=Kosovo, iso=XK |

Handling notes:

- `England`, `Scotland`, `Wales`, `Northern Ireland` → `United Kingdom`, iso `GB`
- `United Kingdom (England, Wales & N. Ireland)` and similar compound UK variants → `United Kingdom`, iso `GB`
- `Gaza Strip`, `West Bank`, `Gaza`, `Palestine`, `Palestinian territories` → iso `PS`
- `Korea` (bare, no qualifier) → South Korea (`KR`); logged at INFO level
- Historical country names (`USSR`, `Yugoslavia`, `Zaire`, `East Germany`, etc.) → preserved in `geo_country`, `geo_iso3166 = HISTORICAL`
- `Turkey` / `Türkiye`, `Namibia`, `Burma`, `DR Congo`, `Russia`, `Czech Republic`, `Svalbard`, `Kosovo`, and several other names are resolved via a hardcoded alias table before pycountry fuzzy lookup. `Kosovo` uses code `XK` — a user-assigned code per CLDR and EU conventions, not part of the official ISO 3166-1 standard.
- All unique `geo_loc_name` values are resolved once and cached; pycountry fuzzy lookup runs at most once per unique country string regardless of row count

---

## One Health classification

`OneHealthClassifier` loads all biological knowledge from `schemas/one_health_dictionaries.json` and assigns each record one of six categories: **Human**, **Animal**, **Plant**, **Food**, **Environmental**, **Unclassified**.

`classify_multi_field()` accepts up to six named `pd.Series` and returns a DataFrame with seven columns:

| Column | Type | Description |
|---|---|---|
| `one_health_category` | str | Assigned category; always a string, never NaN |
| `one_health_term` | str / NaN | The specific term or phrase that triggered the classification |
| `one_health_confidence` | float | Score in [0, 1]; computed as `term_specificity × field_weight + corroboration_bonus` |
| `one_health_evidence_level` | str | `high` (≥0.85), `medium` (≥0.60), `low` (≥0.30), `unresolved` |
| `one_health_processing` | str / NaN | Processing/handling term detected in the text (e.g. `pasteurized`, `frozen`) |
| `one_health_setting` | str / NaN | Setting term detected in the text (e.g. `clinical`, `farm`, `retail`) |
| `one_health_source_field` | str / NaN | Input field that produced the winning classification |

**Confidence model.** For each field, `confidence = min(1.0, term_specificity × field_weight + corroboration_bonus)`:

- `term_specificity`: 1.0 for host dictionary or unambiguous list hits; 0.90/0.75/0.50 for tier1 phrases by length; `WRatio / 100` for rapidfuzz fallback; 0.30 for ambiguous terms.
- `field_weight`: `isolation_source` / host dict hit → 1.00; host text hit → 0.90; `env_medium` → 0.85; `env_local_scale` → 0.80; `sample_type` → 0.70; `env_broad_scale` → 0.50.
- `corroboration_bonus`: +0.10 when a second independent field agrees with the same category.

**Classification pipeline per record:**

1. `host` field: institution guard (strips culture collection prefixes; returns Lab if residual < 4 chars), then bracket/parenthesis annotation stripping (e.g. `[NCBITaxon:9825]`, `(Linnaeus 1758)`), then `host_to_category` dictionary lookup with progressive right-token-drop fallback for trinomial/subspecies names, then text classification fallback.
2. `isolation_source`, `env_medium`, `env_local_scale`: matched against unambiguous human/animal term lists, then tier1 patterns, then rapidfuzz fuzzy fallback against the ontology map.
3. `sample_type`: domain-level signal; used to set category if no specimen field matched.
4. `env_broad_scale`: supporting signal only; contributes a corroboration bonus but does not set the primary category on its own.
5. Pass 2 resolves the winning category from accumulated domain/specimen/supporting evidence.

**Host trinomial / subspecies fallback.** When a host value like `Equus ferus caballus` is not found as an exact entry in `host_to_category`, the classifier progressively drops tokens from the right (`Equus ferus`, then `Equus`) until a match is found or all prefixes are exhausted. This fallback is active only when every token in the name is composed solely of letters or hyphens (no digits or strain identifiers), so free-text phrases are never misclassified via this path. The bundled `one_health_dictionaries.json` contains only a hand-curated seed; after running `scripts/build_dictionaries.py` the full NCBI taxonomy is present and the fallback is rarely needed.

---

## `collected_by` priority

1. **Explicit BioSample attribute** — any `<Attribute harmonized_name="collected_by">` or synonym is always preferred.
2. **`<Owner/Name>` fallback** — used only if no explicit collector attribute was found.

When both are present, the submission-side provenance is written to `_extra_attributes`:

- `submission_owner` — `<Owner/Name>` value
- `submission_contact` — full name from `<Owner/Contacts/Contact>`

---

## Output formats

```python
from biometaharmonizer import write, write_summary

write(df, "out.csv")                        # CSV
write(df, "out.tsv", fmt="tsv")             # TSV
write(df, "out.xlsx", fmt="excel")          # Excel
write(df, "out.parquet", fmt="parquet")     # Parquet
write(df, "out.jsonl", fmt="jsonl")         # JSON Lines (one record per line)

write_summary(df, "fill_rates.csv")         # column, non_null_count, fill_pct
```

Format strings are case-insensitive. If `--format` is not specified on the CLI, the format is inferred from the output file extension (`.jsonl` → `jsonl`).

The JSONL writer decodes the `_extra_attributes` field from its JSON string representation into a native Python dict before serialization, so downstream consumers receive a fully nested JSON object rather than a double-encoded string.

### Multi-format output

Pass multiple space-separated format names to `--format` to write all formats in a single pipeline run. The stem of `--output` is reused and the correct extension is substituted automatically:

```bash
biometaharmonizer run \
    --input ids.txt \
    --email you@example.com \
    --output results/harmonized.csv \
    --format csv tsv parquet jsonl
# Writes:
#   results/harmonized.csv
#   results/harmonized.tsv
#   results/harmonized.parquet
#   results/harmonized.jsonl
```

When only a single format is given, the `--output` path is used exactly as specified.

| Format | Extension substituted |
|---|---|
| `csv` | `.csv` |
| `tsv` | `.tsv` |
| `excel` | `.xlsx` |
| `parquet` | `.parquet` |
| `jsonl` | `.jsonl` |

---

## Rebuilding schema files

The package ships with pre-built schema files. These are sufficient for basic use, but rebuilding them is strongly recommended before processing large or taxonomically diverse datasets.

### `build_dictionaries.py` — One Health dictionary

The bundled `one_health_dictionaries.json` is a minimal hand-curated seed. It covers common host names and key ontology terms, but it does **not** include the full NCBI taxonomy. Without rebuilding, trinomial host names (e.g. `Equus ferus caballus`, `Bos taurus indicus`) and many uncommon species will fall back to the progressive prefix-drop heuristic instead of resolving from an authoritative entry.

To build the full dictionary, run:

```bash
python scripts/build_dictionaries.py \
    --base   src/biometaharmonizer/schemas/one_health_dictionaries.json \
    --output src/biometaharmonizer/schemas/one_health_dictionaries.json
```

This queries the OLS4 API (ENVO, FoodOn, UBERON, Plant Ontology) and downloads the NCBI taxonomy dump (~65 MB) to populate `host_to_category` with scientific names, common names, and equivalent names for all vertebrates and plants. The full build takes a few minutes depending on network speed.

Options:

| Flag | Description |
|---|---|
| `--taxdmp PATH` | Path to a pre-downloaded `taxdmp.zip` or an extracted directory containing `names.dmp` and `nodes.dmp`. Skips the ~65 MB NCBI download. |
| `--skip-ncbi` | Skip NCBI taxonomy entirely (OLS4 terms only). |
| `--skip-ols` | Skip OLS4 queries (NCBI taxonomy only). |
| `--umls-key KEY` | Optional UMLS API key for additional synonym expansion. |

The hand-curated entries in the base file always win over ontology-derived data (`merge_strategy: base_wins`).

### `build_ncbi_attribute_cache.py`

Downloads the official NCBI BioSample attribute harmonization table and stores it as `schemas/ncbi_attributes.xml`.

```bash
python scripts/build_ncbi_attribute_cache.py
```

---

## Scripts

### `generate_summary_report.py`

Generates an interactive, self-contained HTML report from a BioMetaHarmonizer
output file. The report includes metadata completeness (fill rates), geographic
distribution, temporal trends, taxonomy, One Health breakdown, and a searchable
paginated data table — all embedded in a single HTML file with no server required.

```bash
# Output defaults to harmonized_report.html next to the input file
python scripts/generate_summary_report.py harmonized.csv

# Specify a custom output path
python scripts/generate_summary_report.py harmonized.csv report.html
```

| Argument | Description |
|---|---|
| `input` | Path to a BioMetaHarmonizer output file (`.csv`, `.tsv`, `.xlsx`, `.parquet`) |
| `output` | (optional) Output `.html` path. Defaults to `<stem>_report.html` next to the input. |

Requires `plotly` (loaded from CDN — no local install needed at runtime) and `pandas`. For Excel input, also install `openpyxl`.

---

## Repository structure

```
BioMetaHarmonizer/
├── src/biometaharmonizer/
│   ├── __init__.py             # public API, version 1.0.0
│   ├── cli.py                  # CLI entrypoint
│   ├── ingestion.py            # Entrez fetching, XML parsing, schema definition
│   ├── synonyms.py             # two-layer synonym lookup (unified.json + NCBI XML)
│   ├── key_mapper.py           # column rename, coalesce, reindex
│   ├── date_engine.py          # date parsing, ISO 8601 output
│   ├── geo_engine.py           # geo_loc_name splitting, ISO-3166 resolution
│   ├── one_health.py           # One Health categorization
│   ├── output.py               # write CSV / TSV / Excel / Parquet / JSONL
│   └── schemas/
│       ├── unified.json                      # standard keys + synonym lists
│       ├── one_health_dictionaries.json      # One Health keyword/ontology dict
│       └── ncbi_attributes.xml               # NCBI harmonization table (optional)
├── scripts/
│   ├── generate_summary_report.py          # fill-rate + quality HTML/JSON/CSV report
│   ├── build_dictionaries.py               # rebuild one_health_dictionaries.json
│   └── build_ncbi_attribute_cache.py       # rebuild ncbi_attributes.xml
├── tests/
│   ├── test_ingestion.py
│   ├── test_key_mapper.py
│   ├── test_date_engine.py
│   ├── test_geo_engine.py
│   ├── test_one_health.py
│   ├── test_output.py
│   └── test_pipeline.py
└── pyproject.toml
```

---

## Running tests

```bash
pip install pytest
pytest tests/ -v --tb=short
```

All tests use synthetic data — no live NCBI calls are made.

---

### Python API

```python
from biometaharmonizer.ingestion import set_email, ingest
from biometaharmonizer import KeyMapper, DateEngine, GeoEngine, OneHealthClassifier
from biometaharmonizer import write, write_summary

# Ingest: accepts a file path, a Python list, or a mix of both accession types
set_email("your@email.com")
df = ingest("accessions.txt")
# or: df = ingest(["SAMN12345678", "GCF_000001405.39"])

# Force re-download of assembly summary flat files (bypasses 7-day TTL):
# df = ingest("accessions.txt", refresh_cache=True)

# Key harmonization — renames raw columns to standard keys, coalesces duplicates
# Needed only if you bring your own DataFrame; ingest() already applies the schema
mapper = KeyMapper()
df = mapper.map_columns(df)

# Date parsing: 40+ input formats -> ISO 8601 (YYYY / YYYY-MM / YYYY-MM-DD)
de = DateEngine()
date_df = de.parse_with_range(df["collection_date"])
df["collection_date"] = date_df["collection_date"]
df["collection_date_range"] = date_df["collection_date_range"]

# Geography: splits geo_loc_name into country, region, locality, ISO code, sea
ge = GeoEngine()
geo_df = ge.parse(df["geo_loc_name"])
for col in geo_df.columns:
    df[col] = geo_df[col]

# One Health classification across multiple source columns simultaneously
oh = OneHealthClassifier()
src = {col: df[col] for col in
       ["isolation_source", "env_broad_scale", "env_local_scale",
        "env_medium", "sample_type", "host"]
       if col in df.columns}
oh_df = oh.classify_multi_field(**src)
for col in oh_df.columns:
    df[col] = oh_df[col]

# Write output
write(df, "harmonized.csv")
write_summary(df, "fill_rates.csv")
```

---

## License

MIT
