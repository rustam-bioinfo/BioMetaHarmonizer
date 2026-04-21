# BioMetaHarmonizer

[![version](https://img.shields.io/badge/version-0.4.0-blue)](#)
[![python](https://img.shields.io/badge/python-3.9%2B-blue)](#)
[![license](https://img.shields.io/badge/license-MIT-green)](#)

A universal Python package for harmonizing, parsing, and standardizing NCBI BioSample metadata for large-scale genomic epidemiology.

## Overview

The NCBI BioSample database is the central repository for genomic metadata. Because submissions are predominantly free-text and crowd-sourced, the metadata is highly unstructured across thousands of submitters. **BioMetaHarmonizer** resolves this by providing a lightweight, pip-installable pipeline that:

- Fetches BioSample XML records directly from NCBI Entrez for any list of BioSample or assembly accessions
- Resolves BioProject and assembly accessions (GCF_ / GCA_) from NCBI assembly summary flat files
- Produces a **fixed, deterministic output schema** — every run on any dataset outputs the same columns in the same order, regardless of what attributes individual submitters included
- Maps raw free-text attribute variants to standard keys using the **official NCBI BioSample `harmonized_name` XML attribute** as the primary signal, with a two-layer synonym lookup (unified.json + NCBI attribute table) as fallback
- Any attribute that does not resolve to a known schema column is preserved losslessly in `_extra_attributes` as a JSON string — no data is ever discarded
- Parses dates (40+ formats → ISO 8601), resolves ISO-3166 country/region geography, and classifies isolation source into One Health categories (Human / Animal / Food / Environmental / Lab) — all in-place into the fixed schema columns
- Writes harmonized output to CSV, TSV, Excel, or Parquet

## Installation

```bash
git clone https://github.com/rustam-bioinfo/BioMetaHarmonizer.git
cd BioMetaHarmonizer
pip install -e .
```

## Quick Start

### Command line

```bash
biometaharmonizer run \
    --input  accessions.txt \
    --email  your@email.com \
    --output harmonized.csv
```

Optional flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--api-key KEY` | — | NCBI API key (raises rate limit to 10 req/s) |
| `--cache-dir DIR` | `~/.biometaharmonizer/cache/` | Assembly summary cache directory |
| `--format FORMAT` | inferred from extension | `csv`, `tsv`, `excel`, `parquet` (case-insensitive) |
| `--summary FILE` | — | Write per-column fill-rate CSV |
| `--verbose` | off | Enable DEBUG logging |

### Python API

```python
from biometaharmonizer.ingestion import set_email, ingest
from biometaharmonizer import KeyMapper, DateEngine, GeoEngine, OneHealthClassifier, write, write_summary

# 1. Ingest — accepts BioSample IDs, assembly accessions, or a mixed file
#    Output is already a fixed-schema DataFrame: every column always present.
set_email("your@email.com")
df = ingest("accessions.txt")

# 2. Harmonize column names (for non-ingestion/custom workflows)
#    Renames raw columns to standard keys, coalesces any duplicates,
#    and reindexes to the fixed schema.
mapper = KeyMapper()
df = mapper.map_columns(df)

# 3. Parse dates → ISO 8601 truncated (YYYY / YYYY-MM / YYYY-MM-DD)
#    Written in-place into collection_date and collection_date_range columns.
de = DateEngine()
date_df = de.parse_with_range(df["collection_date"])
df["collection_date"] = date_df["collection_date"]
df["collection_date_range"] = date_df["collection_date_range"]

# 4. Resolve geography → in-place into geo_country, geo_region, etc.
ge = GeoEngine()
geo_df = ge.parse(df["geo_loc_name"])
for col in geo_df.columns:
    df[col] = geo_df[col]

# 5. Classify isolation source + host → in-place into one_health_category
oh = OneHealthClassifier()
df["one_health_category"] = oh.classify_joint(df["isolation_source"], df["host"])

# 6. Write output
write(df, "harmonized.csv")
write_summary(df, "fill_rates.csv")

print(df.shape)   # (N_records, fixed number of columns — always identical)
```

## Output Schema

Every output file contains exactly the following columns, in this order, regardless of dataset.
Attributes that are not part of the fixed schema are preserved in `_extra_attributes` as a JSON
string — including low-frequency NCBI attributes such as `antimicrobial_resistance`, `temp`, `ph`,
`depth`, `elev`, `samp_size`, and `samp_mat_process`.

| # | Column | Source |
|---|--------|--------|
| 1 | `biosample_accession` | BioSample XML structural field |
| 2 | `biosample_id` | BioSample XML structural field |
| 3 | `sra_accession` | BioSample XML structural field |
| 4 | `bioproject_accession` | BioSample XML / assembly index |
| 5 | `assembly_accession_refseq` | Assembly index (GCF_) |
| 6 | `assembly_accession_genbank` | Assembly index (GCA_) |
| 7 | `sample_name_id` | BioSample XML structural field |
| 8 | `taxonomy_id` | BioSample XML structural field |
| 9 | `taxonomy_name` | BioSample XML structural field |
| 10 | `organism_name` | BioSample XML structural field |
| 11 | `collection_date` | BioSample attribute → DateEngine |
| 12 | `collection_date_range` | DateEngine output |
| 13 | `geo_loc_name` | BioSample attribute |
| 14 | `lat_lon` | BioSample attribute |
| 15 | `geo_country` | GeoEngine output |
| 16 | `geo_region` | GeoEngine output |
| 17 | `geo_locality` | GeoEngine output |
| 18 | `geo_iso3166` | GeoEngine output |
| 19 | `geo_sea_ocean` | GeoEngine output |
| 20 | `geo_loc_raw` | GeoEngine output (coordinate-only inputs) |
| 21 | `host` | BioSample attribute → OneHealthClassifier |
| 22 | `host_disease` | BioSample attribute |
| 23 | `host_age` | BioSample attribute |
| 24 | `host_sex` | BioSample attribute |
| 25 | `host_tissue_sampled` | BioSample attribute |
| 26 | `isolation_source` | BioSample attribute → OneHealthClassifier |
| 27 | `one_health_category` | OneHealthClassifier output |
| 28 | `isolate` | BioSample attribute |
| 29 | `sub_strain` | BioSample attribute |
| 30 | `serotype` | BioSample attribute |
| 31 | `serovar` | BioSample attribute |
| 32 | `genotype` | BioSample attribute |
| 33 | `culture_collection` | BioSample attribute |
| 34 | `outbreak` | BioSample attribute |
| 35 | `env_broad_scale` | BioSample attribute |
| 36 | `env_local_scale` | BioSample attribute |
| 37 | `env_medium` | BioSample attribute |
| 38 | `sequencing_method` | BioSample attribute |
| 39 | `assembly_method` | BioSample attribute |
| 40 | `collected_by` | BioSample attribute |
| 41 | `ncbi_package` | BioSample XML structural field |
| 42 | `submission_date` | BioSample XML structural field |
| 43 | `last_update` | BioSample XML structural field |
| 44 | `publication_date` | BioSample XML structural field |
| 45 | `access` | BioSample XML structural field |
| 46 | `status` | BioSample XML structural field |
| 47 | `status_date` | BioSample XML structural field |
| 48 | `title` | BioSample XML structural field |
| 49 | `description_comment` | BioSample XML structural field |
| 50 | `_extra_attributes` | JSON dict of all unresolved submitter attributes |

Columns that have no data for a given dataset are present but filled with `NaN`. No columns are ever added, dropped, or reordered at runtime.

## Architecture

```
BioMetaHarmonizer/
├── src/biometaharmonizer/
│   ├── __init__.py             # version 0.4.0, full public API
│   ├── cli.py                  # CLI entrypoint: biometaharmonizer run
│   ├── ingestion.py            # Module 1: fixed schema, XML parsing, BioProject resolution
│   ├── synonyms.py             # Shared two-layer synonym lookup (unified.json + NCBI XML)
│   ├── key_mapper.py           # Module 2: column rename, coalesce, reindex to fixed schema
│   ├── date_engine.py          # Module 3: temporal parsing (40+ formats → ISO 8601)
│   ├── geo_engine.py           # Module 4: ISO-3166 geospatial resolution
│   ├── one_health.py           # Module 5: One Health categorization (Tier 1 Regex)
│   ├── output.py               # Module 6: write CSV / TSV / Excel / Parquet
│   └── schemas/
│       ├── ncbi_attributes.xml         # NCBI official harmonization table (Layer 2, optional)
│       └── unified.json                # standard_key definitions + synonym lists (Layer 1)
├── scripts/
│   └── build_ncbi_attribute_cache.py   # optional: rebuild ncbi_attributes.xml from NCBI
├── tests/
│   ├── conftest.py
│   ├── test_ingestion.py
│   ├── test_key_mapper.py
│   ├── test_date_engine.py
│   ├── test_geo_engine.py
│   ├── test_one_health.py
│   ├── test_output.py
│   └── test_pipeline.py
├── docs/SESSION_PROTOCOL.md
├── pyproject.toml
└── requirements.txt
```

## Modules

| Module | File | Status | Notes |
|---|---|---|---|
| 1. Ingestion | `ingestion.py` | Complete | Fixed schema defined here; BioProject and assembly accessions resolved via assembly index |
| Synonym Lookup | `synonyms.py` | Complete | Single shared two-layer lookup used by ingestion + key_mapper; result cached per process |
| 2. Key Harmonization | `key_mapper.py` | Complete | Rename raw columns to standard keys, coalesce duplicates, reindex to fixed schema |
| 3. Temporal Parsing | `date_engine.py` | Complete | 40+ date formats, ISO 8601 output |
| 4. Geospatial Resolution | `geo_engine.py` | Complete | ISO-3166 country, region, locality; assigned in-place |
| 5. One Health Categorization | `one_health.py` | Complete | Human / Animal / Food / Environmental / Lab |
| 6. Output | `output.py` | Complete | CSV, TSV, Excel, Parquet; fill-rate summary |

## Attribute Resolution: How It Works

When parsing a live BioSample XML record, each `<Attribute>` element is resolved in the following priority order:

1. **NCBI `harmonized_name` (authoritative)** — if the `harmonized_name` attribute in the XML element matches a known final output column directly, it is used without any synonym lookup. This is the signal NCBI itself assigns and is the most reliable mapping available.
2. **Synonym lookup on `harmonized_name`** — if the `harmonized_name` is not a direct schema column but appears in the unified synonym table, the resolved standard key is used. If the resolved key is not in the fixed schema, the normalized key name is stored in `_extra_attributes` (not the raw alias).
3. **Synonym lookup on `attribute_name`** — if `harmonized_name` is absent or unresolvable, the raw `attribute_name` is looked up in the synonym table.
4. **`_extra_attributes`** — any attribute that cannot be resolved by any of the above is serialized as a JSON key-value pair into the `_extra_attributes` column. No data is discarded.

The synonym table (`synonyms.py`) is built from two layers on startup and cached for the lifetime of the process:

- **Layer 1 — `unified.json`** — manually curated synonym lists for all standard keys
- **Layer 2 — `ncbi_attributes.xml`** — the official NCBI BioSample harmonization table; loaded on top of Layer 1 and wins on any conflict

Both `ingestion.py` and `key_mapper.py` import the same `build_synonym_lookup()` function, so the mapping is always identical across both modules.

## Geospatial Parsing

`GeoEngine` accepts the standard NCBI `geo_loc_name` field and fills four in-schema columns:
`geo_country`, `geo_region`, `geo_locality`, and `geo_iso3166` (ISO 3166-1 alpha-2 code).
For coordinate-only inputs, the raw value is preserved in `geo_loc_raw`.

Supported input formats:

| Input | Parsed as |
|---|---|
| `"USA: California, Los Angeles"` | country=USA, region=California, locality=Los Angeles |
| `"USA: California"` | country=USA, region=California |
| `"Germany, Bavaria"` | country=Germany, locality=Bavaria |
| `"France"` | country=France |
| `"Pacific Ocean"` | geo_sea_ocean=Pacific Ocean, country columns left empty |
| `"40.71 N, 74.00 W"` or `"40.7128, -74.0060"` | preserved in `geo_loc_raw`, country columns left empty |

UK sub-country names (England, Scotland, Wales, Northern Ireland) are normalised to `"United Kingdom"` with ISO code `GB`. Ambiguous `"Korea"` defaults to South Korea (`KR`) with a warning logged.

## Input Formats

| Format | Example |
|---|---|
| BioSample ID file | `SAMN12345678` one per line |
| Assembly accession file | `GCF_000001405.39` one per line |
| Mixed file | both types in the same file |
| Python list | `ingest(["SAMN12345678", "GCF_000001405.39"])` |

## Output

```python
from biometaharmonizer import write, write_summary

write(df, "harmonized.csv")                  # default: CSV
write(df, "harmonized.xlsx", fmt="excel")    # Excel
write(df, "harmonized.parquet", fmt="parquet")

write_summary(df, "fill_rates.csv")          # column_name, non_null_count, fill_pct
```

Output format strings are case-insensitive (`"CSV"`, `"csv"`, and `"Csv"` are all accepted).

## Running Tests

```bash
pip install pytest
pytest tests/ -v --tb=short
pytest tests/ -v --cov=biometaharmonizer --cov-report=term-missing
```

All tests are self-contained (no live NCBI calls). Current: **170/170 passing**.

## License

MIT
