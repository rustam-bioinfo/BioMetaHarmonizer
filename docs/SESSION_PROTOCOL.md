# BioMetaHarmonizer Session Restoration Protocol

Copy and paste the block below at the start of any new AI-assisted session to restore full project context.

---

## [SYSTEM PROTOCOL: BioMetaHarmonizer Project]

### 1. Project Identity & Goal
- **Project Name:** BioMetaHarmonizer
- **Repository:** https://github.com/rustam-bioinfo/BioMetaHarmonizer
- **Version:** 0.3.1
- **Objective:** Develop a Python 3.9+ pip-installable package to dynamically harmonize, parse, and categorize messy NCBI BioSample metadata (parsing dates, resolving ISO-3166 geographies, standardizing categorical variables).
- **End Goal:** Publication as an Application Note in *Bioinformatics*, *GigaScience*, or *Microbial Genomics*.
- **Primary Test Data:** A dataset of 6,618 *B. cereus* group genomes. Ingestion yields 6,508 records (6,618 - 110 fetch failures). After KeyMapper: 6,508 x 195 columns.

### 2. Core Architecture Rules
- **Config-Driven Design:** One set of core parsing modules driven by external JSON/XML data. We DO NOT write separate Python functions per biological schema.
- **Workflow:** Ingestion -> Key Harmonization -> Value Parsing -> Categorization -> Output.
- **Input:** Two accepted formats -- a plain `.txt` list of BioSample IDs (SAMN/SAME/SAMD) or assembly accessions (GCF_/GCA_). Mixed files handled automatically.
- **Dependencies:** `pandas`, `numpy`, `sentence-transformers`, `pycountry`, `python-dateutil`, `biopython`, `requests`, `openpyxl`, `pyarrow`.
- **Structural Fields (bypass KeyMapper):** Always extracted directly from BioSample XML -- never passed through synonym resolution:
  `biosample_accession`, `biosample_id`, `sra_accession`, `bioproject_accession`,
  `taxonomy_id`, `taxonomy_name`, `organism_name`,
  `submission_date`, `last_update`, `publication_date`, `access`,
  `status`, `status_date`, `title`, `description_comment`, `ncbi_package`, `sample_name_id`.
- **BioProject resolution:** BioProject accession is absent from BioSample XML. Resolved via NCBI assembly summary flat files (RefSeq + GenBank) downloaded once to disk (~100 MB each). `_ensure_assembly_summaries()` is called unconditionally in `ingest()` regardless of input ID type.
- **Working directory:** Assembly summary flat files are cached in `~/.biometaharmonizer/cache/` by default. Override with `set_cache_dir("/content")` in Colab.

### 3. Module Tracking
- [x] **Module 1 (Ingestion):** `src/biometaharmonizer/ingestion.py` -- COMPLETE.
  - Accepts BioSample IDs, GCF_/GCA_ assembly accessions, or mixed lists/files.
  - Resolves BioProject accession from assembly summary flat files (96.4% fill on B. cereus dataset).
  - Full XML extraction: Ids, Organism, Description, Package, Status, all Attributes.
  - `organism_name` = `taxonomy_name` attribute of `<Organism>` element (no `<OrganismName>` child exists in real NCBI XML).
  - Retry/backoff for failed batches (up to 3 retries with exponential backoff).
  - Configurable API key via `set_api_key()` or `ingest(api_key=...)` for 10 req/s rate limit.
  - Configurable cache directory via `set_cache_dir()` or `ingest(cache_dir=...)`.
  - Input deduplication via `_deduplicate()`.
- [x] **Module 2 (Key Mapper):** `src/biometaharmonizer/key_mapper.py` -- COMPLETE.
  - Option C: NCBI attribute XML (Layer 1) + sentence-transformers all-MiniLM-L6-v2 (Layer 2). rapidfuzz removed.
  - Layer 1: exact/synonym lookup from `schemas/ncbi_attributes.xml` (built by `scripts/build_ncbi_attribute_cache.py`).
  - Layer 2: cosine similarity fallback via precomputed embeddings (`schemas/ncbi_embeddings.npy`). `SEMANTIC_THRESHOLD = 0.75`. Model loaded lazily on first use.
  - Features preserved: `drop_sparse`, `drop_junk`, `_PROTECTED_COLUMNS`, `_coalesce_duplicates`.
  - `_warn_missing_mandatory()` now returns a compliance DataFrame with columns: `package, field, total_records, filled_records, fill_pct, status`. Status thresholds: PASS >= 95%, WARN 80-95%, FAIL < 80%.
  - Dead `get_parser_routing()` method removed.
- [x] **Module 3 (Date Engine):** `src/biometaharmonizer/date_engine.py` -- COMPLETE. 79.3% fill on B. cereus dataset.
  - Outputs proper ISO 8601 truncated dates: year-only -> "YYYY", year-month -> "YYYY-MM", full -> "YYYY-MM-DD". No XX placeholders.
  - INSDC date range parsing: "2019/2020" or "2019-01/2020-03" -> extracts start date, stores full range in `collection_date_range`.
  - Two-digit year guard: rejects strings like "19", "99" with a warning.
  - Expanded NULL_PATTERNS: covers `missing: ...`, `not applicable: ...`, `restricted access`.
  - `parse_with_range()` method returns DataFrame with `collection_date` and `collection_date_range` columns.
- [x] **Module 4 (Geo Engine):** `src/biometaharmonizer/geo_engine.py` -- COMPLETE. 94.9% country, 58.8% region.
  - Output columns are now snake_case: `geo_country`, `geo_region`, `geo_locality`, `geo_iso3166`, `geo_sea_ocean`, `geo_loc_raw`.
  - UK sub-country lookup: "England", "Scotland", "Wales", "Northern Ireland" -> `geo_country="United Kingdom"`, `geo_iso3166="GB"`.
  - Korea ambiguity: bare "Korea" defaults to "KR" (South Korea) with a logged warning.
  - Taiwan: explicit lookup ensures `geo_iso3166="TW"`.
  - Ocean/sea names: detected and routed to `geo_sea_ocean` column (Pacific Ocean, Atlantic Ocean, etc.).
  - Coordinate detection: lat/lon strings skip country parsing, stored in `geo_loc_raw`.
- [x] **Module 5 (One Health):** `src/biometaharmonizer/one_health.py` -- COMPLETE. 76.6% classified. Tier 1 Regex with word-boundary fixes.
  - Pattern specificity: "bovine blood" -> Animal (not Human); "environmental swab" -> Environmental (not Human).
  - Removed false-positive patterns: "sequencing", "genomic" from Lab; "worker" from Animal; "core", "leaf", "root", "seed" from Food.
  - `classify_joint(isolation_source_series, host_series)`: classifies isolation_source first, falls back to host.
  - `classify_with_confidence(series)`: returns DataFrame with `one_health_category`, `one_health_term`, `one_health_confidence` (1.0/0.5/0.0).
- [x] **Module 6 (Output):** `src/biometaharmonizer/output.py` -- COMPLETE. `write()` (csv/tsv/excel/parquet) and `write_summary()` implemented.

### 4. Schema Architecture (current)
- **`schemas/unified.json`** -- LEGACY (superseded by `ncbi_attributes.xml` in v0.3.0). Not loaded by KeyMapper.
- **`schemas/mandatory_fields.json`** -- Maps all 22 `ncbi_package` values (including `Human.1.0`, `Plant.1.0`) to required fields. Includes `default` fallback. RETAINED -- mandatory validation is independent of synonym resolution.
  - `lat_lon` added to all MIGS.ba.* and MIMS.me.* packages.
  - `host` added to MIMS.me.human-gut.6.0.
- **`schemas/pathogen_cl_1.0.json`** -- Legacy. Not loaded by KeyMapper.
- **`schemas/pathogen_env_1.0.json`** -- Legacy. Not loaded by KeyMapper.
- **`schemas/ncbi_attributes.xml`** -- NCBI official attribute harmonization table, fetched by `scripts/build_ncbi_attribute_cache.py`.
- **`schemas/ncbi_embeddings.npy`** -- Precomputed all-MiniLM-L6-v2 embeddings, shape [N, 384], float32.
- **`schemas/ncbi_harmonized_names.json`** -- Sorted list of N harmonized names; row index matches `ncbi_embeddings.npy`.

### 5. Validated Performance (B. cereus, n=6,618)

| Field | Fill rate |
|---|---|
| `biosample_accession` | 100% |
| `organism_name` | 95.8% |
| `bioproject_accession` | 96.4% |
| `collection_date` (parsed) | 79.3% |
| `geo_loc_name` country | 94.9% |
| `geo_loc_name` region | 58.8% |
| `one_health_category` | 76.6% |

Raw shape: 6,508 x 346. Post-KeyMapper: 6,508 x 195.

### 6. KeyMapper Warning Behavior
- Packages with fewer than `MIN_WARN_GROUP_SIZE = 10` records are silently skipped.
- `_warn_missing_mandatory()` returns a compliance DataFrame. Status thresholds: PASS >= 95%, WARN 80-95%, FAIL < 80%.
- Known data quality issues in NCBI (not bugs): `Pathogen.env.1.0` env fields near 0%,
  all `MIGS.ba.*` `isolation_source` near 0%, `Generic.1.0` core fields ~17-19%.
  These reflect widespread NCBI submission non-compliance, not tool failures.

### 7. Test Infrastructure
- `conftest.py` at repo root injects `src/` into `sys.path` for pytest.
- `tests/conftest.py` registers the `network` marker for live NCBI tests.
- Run all tests: `!pytest tests/ -v --tb=short`
- Run with coverage: `!pytest tests/ -v --cov=biometaharmonizer --cov-report=term-missing`
- Run without network tests: `!pytest tests/ -v --ignore=tests/test_ingestion.py` or `!pytest tests/ -v -m "not network"`
- `test_key_mapper.py`: covers `drop_sparse`, `drop_junk`, `_PROTECTED_COLUMNS` protection, per-package mandatory validation (compliance DataFrame), Option C exact-synonym and semantic resolution, and RuntimeError on missing cache.
- `test_output.py`: covers write() (csv/tsv/parquet/excel/invalid), parent dir creation, return type, write_summary() fill rates.
- `test_pipeline.py`: end-to-end integration -- KeyMapper rename, DateEngine, GeoEngine (snake_case columns), OneHealthClassifier, output write.
- `test_date_engine.py`: ISO 8601 truncated output, INSDC range parsing, two-digit year guard, expanded null vocab.
- `test_geo_engine.py`: snake_case columns, UK sub-country, Korea ambiguity, Taiwan, ocean/sea, coordinate detection.
- `test_one_health.py`: pattern specificity (bovine blood, environmental swab), classify_joint(), classify_with_confidence().
- `test_ingestion.py`: load/classify/parse/ingest mocked tests, deduplication, network marker.

### 8. Colab Setup (every session)
```python
import subprocess, sys, os

REPO_DIR = "/content/BioMetaHarmonizer"
SRC_DIR  = f"{REPO_DIR}/src"

subprocess.run(["git", "-C", REPO_DIR, "pull"], check=True)

for key in list(sys.modules.keys()):
    if "biometaharmonizer" in key:
        del sys.modules[key]

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

os.chdir("/content")  # required for assembly summary flat file paths

import biometaharmonizer
print(biometaharmonizer.__version__)  # should print 0.3.1
```

### 9. Next Steps (in priority order)
1. **CLI entrypoint** -- `src/biometaharmonizer/cli.py`
   `biometaharmonizer run --input ids.txt --email user@email.com --output out.csv`
2. **Run `build_ncbi_attribute_cache.py` on real NCBI data** and validate fill rate improvement vs. `unified.json` baseline.
3. **Manuscript benchmarking** -- parse rates per field before/after Option C (baseline = unified.json, test = ncbi_attributes.xml + embeddings). The delta demonstrates the value of the approach.
4. **PyPI release** -- finalize `MANIFEST.in`, test twine upload to TestPyPI.
5. **Application Note draft** -- target *Bioinformatics* / *GigaScience* / *Microbial Genomics*.

### 10. Initialization Command
Please acknowledge receipt of this protocol. Then summarize the current architectural approach in one sentence and state what the next immediate task is. Do not ask what to work on -- begin Option C Step 1 immediately.
