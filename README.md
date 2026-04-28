# BioMetaHarmonizer

[![version](https://img.shields.io/badge/version-0.5.0-blue)](#)
[![python](https://img.shields.io/badge/python-3.9%2B-blue)](#)
[![license](https://img.shields.io/badge/license-MIT-green)](#)

A Python package for fetching, parsing, and standardizing NCBI BioSample metadata for large-scale genomic epidemiology.

---

## What it does

NCBI BioSample metadata is free-text, crowd-sourced, and inconsistent across submitters. BioMetaHarmonizer fetches BioSample XML records via the Entrez API, maps raw attribute names to a fixed set of standard columns, normalizes placeholder null values, parses dates and geographic strings, and assigns One Health categories. The result is a pandas DataFrame that can be written to CSV, TSV, Excel, or Parquet.

Input can be BioSample accessions (`SAMN`, `SAME`, `SAMD`), assembly accessions (`GCF_`, `GCA_`), or a mix of both. Assembly accessions are resolved to BioSample IDs through locally cached NCBI assembly summary flat files.

---

## Installation

```bash
git clone https://github.com/rustam-bioinfo/BioMetaHarmonizer.git
cd BioMetaHarmonizer
pip install -e .
```

Requires Python 3.9+. Dependencies are declared in `pyproject.toml` and installed automatically.

---

## Quick start

### Command line

```bash
biometaharmonizer run \
    --input  accessions.txt \
    --email  your@email.com \
    --output harmonized.csv
```

| Flag | Default | Description |
|---|---|---|
| `--input FILE` | required | Path to accession list (one per line) or comma-separated accessions |
| `--email EMAIL` | required | Valid contact email for NCBI Entrez — must contain `@` and a domain |
| `--output FILE` | required | Output file path |
| `--api-key KEY` | — | NCBI API key; raises rate limit from 3 to 10 requests/second |
| `--cache-dir DIR` | `~/.biometaharmonizer/cache/` | Directory for assembly summary flat files |
| `--format FORMAT` | inferred from file extension | `csv`, `tsv`, `excel`, `parquet` |
| `--summary FILE` | — | Write a per-column fill-rate CSV |
| `--verbose` | off | Enable DEBUG-level logging |

### Python API

```python
from biometaharmonizer.ingestion import set_email, ingest
from biometaharmonizer import KeyMapper, DateEngine, GeoEngine, OneHealthClassifier
from biometaharmonizer import write, write_summary

# Ingest: accepts a file path, a Python list, or a mix of both accession types
set_email("your@email.com")
df = ingest("accessions.txt")
# or: df = ingest(["SAMN12345678", "GCF_000001405.39"])

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

## Output columns

The output DataFrame contains the following 52 columns. Columns with no data for a given dataset are present and filled with `NaN`. Attributes that do not map to any column are preserved as a JSON string in `_extra_attributes`.

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
| 12 | `collection_date_range` | DateEngine | Inferred date range when only year or year-month was provided |
| 13 | `geo_loc_name` | BioSample attribute | Raw geographic location string as submitted |
| 14 | `lat_lon` | BioSample attribute | Decimal lat/lon as submitted |
| 15 | `geo_country` | GeoEngine | Country resolved from geo_loc_name |
| 16 | `geo_region` | GeoEngine | Sub-national region (state, province, oblast) |
| 17 | `geo_locality` | GeoEngine | City or locality |
| 18 | `geo_iso3166` | GeoEngine | ISO 3166-1 alpha-2 country code; historical names tagged `HISTORICAL` |
| 19 | `geo_sea_ocean` | GeoEngine | Sea or ocean name for marine locations |
| 20 | `geo_loc_raw` | GeoEngine | Preserved raw value when only coordinates were provided |
| 21 | `host` | BioSample attribute | Host organism name |
| 22 | `host_disease` | BioSample attribute | Disease associated with host at sampling |
| 23 | `host_age` | BioSample attribute | Age of host |
| 24 | `host_sex` | BioSample attribute | Biological sex of host |
| 25 | `host_tissue_sampled` | BioSample attribute | Tissue or body site sampled |
| 26 | `isolation_source` | BioSample attribute | Material or environment from which the isolate was obtained |
| 27 | `sample_type` | BioSample attribute | Sample type or specimen classification |
| 28 | `one_health_category` | OneHealthClassifier | Human / Animal / Food / Environmental / Lab |
| 29 | `isolate` | BioSample attribute | Isolate identifier |
| 30 | `strain` | BioSample attribute | Strain designation |
| 31 | `sub_strain` | BioSample attribute | Sub-strain designation |
| 32 | `serotype` | BioSample attribute | Serotype |
| 33 | `serovar` | BioSample attribute | Serovar |
| 34 | `genotype` | BioSample attribute | Genotype or sequence type |
| 35 | `culture_collection` | BioSample attribute | Culture collection identifier |
| 36 | `outbreak` | BioSample attribute | Outbreak identifier |
| 37 | `env_broad_scale` | BioSample attribute | Broad environmental context (ENVO) |
| 38 | `env_local_scale` | BioSample attribute | Local environmental feature (ENVO) |
| 39 | `env_medium` | BioSample attribute | Environmental medium (ENVO) |
| 40 | `sequencing_method` | BioSample attribute | Sequencing platform |
| 41 | `assembly_method` | BioSample attribute | Genome assembly software |
| 42 | `collected_by` | BioSample attribute; `<Owner/Name>` fallback | Collector name or institution |
| 43 | `ncbi_package` | BioSample XML | NCBI BioSample package (e.g. `Microbe.1.0`) |
| 44 | `submission_date` | BioSample XML | Date first submitted |
| 45 | `last_update` | BioSample XML | Date last modified |
| 46 | `publication_date` | BioSample XML | Date made publicly available |
| 47 | `access` | BioSample XML | `public` or `controlled-access` |
| 48 | `status` | BioSample XML | Record status (e.g. `live`, `suppressed`) |
| 49 | `status_date` | BioSample XML | Date current status was assigned |
| 50 | `title` | BioSample XML | Free-text title of the BioSample record |
| 51 | `description_comment` | BioSample XML | Free-text description or comment block |
| 52 | `_extra_attributes` | JSON | All attributes that could not be mapped to a schema column, serialized as a JSON dict; also contains `submission_owner` and `submission_contact` when `<Owner>` provenance is present alongside an explicit collector |

---

## Attribute resolution order

For each `<Attribute>` element in BioSample XML, the column mapping is resolved in this order:

1. **`harmonized_name` direct match** — if the NCBI-assigned `harmonized_name` matches a schema column exactly, it is used without any synonym lookup.
2. **Synonym lookup on `harmonized_name`** — if not a direct match, the `harmonized_name` is looked up in the synonym table. If the resolved key is in the schema, it is used; otherwise the resolved key is stored in `_extra_attributes`.
3. **Synonym lookup on `attribute_name`** — if `harmonized_name` is absent or unresolvable, the raw `attribute_name` is tried.
4. **`_extra_attributes`** — any attribute that could not be resolved by any of the above is written to `_extra_attributes` as a JSON key-value pair.

The synonym table is built from two layers in `synonyms.py` and cached for the lifetime of the process:

- **Layer 1 — `schemas/unified.json`** — manually curated synonym lists for all standard keys.
- **Layer 2 — `schemas/ncbi_attributes.xml`** — the official NCBI BioSample harmonization table. Optional; loaded only if present. Generate it with `python scripts/build_ncbi_attribute_cache.py`.

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

These are cached in `~/.biometaharmonizer/cache/` (overridable with `--cache-dir` or `set_cache_dir()`). Files older than 7 days are automatically deleted and re-downloaded on the next run. To force a refresh manually, delete the cached files.

In Colab:

```python
from biometaharmonizer.ingestion import set_cache_dir
set_cache_dir("/content/bmh_cache")
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

`GeoEngine` splits `geo_loc_name` into `geo_country`, `geo_region`, `geo_locality`, `geo_iso3166`, `geo_sea_ocean`, and `geo_loc_raw`.

| Input | Result |
|---|---|
| `"USA: California, Los Angeles"` | country=USA, region=California, locality=Los Angeles, iso=US |
| `"Germany, Bavaria"` | country=Germany, locality=Bavaria, iso=DE |
| `"France"` | country=France, iso=FR |
| `"Pacific Ocean"` | sea_ocean=Pacific Ocean |
| `"Pacific Ocean: Mariana Trench"` | sea_ocean=Pacific Ocean, locality=Mariana Trench |
| `"40.71 N, 74.00 W"` | geo_loc_raw preserved; country/region/locality empty |
| `"not applicable"` | all geo columns empty |

Handling notes:

- `England`, `Scotland`, `Wales`, `Northern Ireland` → `United Kingdom`, iso `GB`
- `Korea` → South Korea (`KR`) with a warning logged
- Historical country names (`USSR`, `Yugoslavia`, etc.) → preserved in `geo_country`, `geo_iso3166 = HISTORICAL`
- Coordinate-only strings are preserved in `geo_loc_raw` and not reverse-geocoded

---

## One Health classification

`OneHealthClassifier` assigns one of five categories: **Human**, **Animal**, **Food**, **Environmental**, **Lab**.

Up to six columns are scored simultaneously: `isolation_source`, `env_broad_scale`, `env_local_scale`, `env_medium`, `sample_type`, `host`. Each column is scored independently against Tier 1 keyword patterns; the highest-confidence match across all columns wins. The CLI pipeline uses multi-field mode automatically.

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

write_summary(df, "fill_rates.csv")         # column, non_null_count, fill_pct
```

Format strings are case-insensitive. If `--format` is not specified on the CLI, the format is inferred from the output file extension.

---

## Repository structure

```
BioMetaHarmonizer/
├── src/biometaharmonizer/
│   ├── __init__.py             # public API, version 0.5.0
│   ├── cli.py                  # CLI entrypoint
│   ├── ingestion.py            # Entrez fetching, XML parsing, schema definition
│   ├── synonyms.py             # two-layer synonym lookup (unified.json + NCBI XML)
│   ├── key_mapper.py           # column rename, coalesce, reindex
│   ├── date_engine.py          # date parsing, ISO 8601 output
│   ├── geo_engine.py           # geo_loc_name splitting, ISO-3166 resolution
│   ├── one_health.py           # One Health categorization
│   ├── output.py               # write CSV / TSV / Excel / Parquet
│   └── schemas/
│       ├── unified.json                      # standard keys + synonym lists
│       └── ncbi_attributes.xml               # NCBI harmonization table (optional)
├── scripts/
│   └── build_ncbi_attribute_cache.py         # regenerate ncbi_attributes.xml
├── tests/
│   ├── test_ingestion.py
│   ├── test_key_mapper.py
│   ├── test_date_engine.py
│   ├── test_geo_engine.py
│   ├── test_one_health.py
│   ├── test_output.py
│   └── test_pipeline.py
├── pyproject.toml
└── requirements.txt
```

---

## Running tests

```bash
pip install pytest
pytest tests/ -v --tb=short
```

All tests use synthetic data — no live NCBI calls are made.

---

## License

MIT
