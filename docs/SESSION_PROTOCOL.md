# BioMetaHarmonizer Session Restoration Protocol

Copy and paste the block below at the start of any new AI-assisted session to restore full project context.

---

## [SYSTEM PROTOCOL: BioMetaHarmonizer Project]

### 1. Project Identity & Goal
- **Project Name:** BioMetaHarmonizer
- **Repository:** https://github.com/rustam-bioinfo/BioMetaHarmonizer
- **Version:** 0.3.0
- **Objective:** Develop a Python 3.9+ pip-installable package to dynamically harmonize, parse, and categorize messy NCBI BioSample metadata (parsing dates, resolving ISO-3166 geographies, standardizing categorical variables).

### 2. Core Architecture Rules
- **Config-Driven Design:** One set of core parsing modules driven by external JSON/XML data. We DO NOT write separate Python functions per biological schema.
- **Workflow:** Ingestion -> Key Harmonization -> Value Parsing -> Categorization -> Output.
- **Input:** Two accepted formats -- a plain `.txt` list of BioSample IDs (SAMN/SAME/SAMD) or assembly accessions (GCF_/GCA_). Mixed files handled automatically. Also accepts a comma-separated string of accessions directly on the CLI.
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
  - Resolves BioProject accession from assembly summary flat files.
  - Full XML extraction: Ids, Organism, Description, Package, Status, all Attributes.
  - `organism_name` prefers `<OrganismName>` child element; falls back to `taxonomy_name` attribute.
  - Retry/backoff for failed batches (up to 3 retries with exponential backoff).
  - Configurable API key via `set_api_key()` or `ingest(api_key=...)` for 10 req/s rate limit.
  - Configurable cache directory via `set_cache_dir()` or `ingest(cache_dir=...)`.
  - Input deduplication via `_deduplicate()`.

- [x] **Module 2 (Key Mapper):** `src/biometaharmonizer/key_mapper.py` -- COMPLETE.
  - Layer 1: exact/synonym lookup from `schemas/ncbi_attributes.xml` (built by `scripts/build_ncbi_attribute_cache.py`).
  - Layer 2: cosine similarity fallback via precomputed embeddings (`schemas/ncbi_embeddings.npy`). `SEMANTIC_THRESHOLD = 0.75`. Model loaded lazily on first use. Model name read from `ncbi_cache_meta.json`; overridable at `KeyMapper(model=...)` construction time.
  - Features: `drop_sparse` (int absolute count or float 0-1 fractional fill rate), `drop_junk`, `_PROTECTED_COLUMNS`, `_coalesce_duplicates`.
  - `_warn_missing_mandatory()` returns a compliance DataFrame: `package, field, total_records, filled_records, fill_pct, status`. Thresholds: PASS >= 95%, WARN 80-95%, FAIL < 80%.
  - Packages with fewer than `MIN_WARN_GROUP_SIZE = 10` records are silently skipped.
  - rapidfuzz removed; dead `get_parser_routing()` method removed.

- [x] **Module 3 (Date Engine):** `src/biometaharmonizer/date_engine.py` -- COMPLETE.
  - Outputs ISO 8601 truncated dates: year-only -> "YYYY", year-month -> "YYYY-MM", full -> "YYYY-MM-DD". No XX placeholders.
  - INSDC date range parsing: "2019/2020" or "2019-01/2020-03" -> start date in `collection_date`, full range in `collection_date_range`.
  - `parse_with_range()` returns a DataFrame with `collection_date` and `collection_date_range` columns.
  - Expanded NULL_PATTERNS: covers `missing: ...`, `not applicable: ...`, `restricted access`.
  - **Bug fixes applied:**
    - Null guard corrected: `pd.isna(value)` (was `not isinstance(value, str) and pd.isna(value)`), so `None` and other non-string NA types are caught before `str()` conversion.
    - Check order corrected: INSDC_RANGE match runs before TWO_DIGIT_YEAR guard so year-only ranges like `"19/20"` are not prematurely rejected.
    - `logger.warning()` added on `ValueError`/`OverflowError` in `_parse_date_string` and `_resolve_year_month` so malformed inputs (e.g. `"2021-13"`) are visible in the log instead of silently returning `NaN`.

- [x] **Module 4 (Geo Engine):** `src/biometaharmonizer/geo_engine.py` -- COMPLETE.
  - Output columns: `geo_country`, `geo_region`, `geo_locality`, `geo_iso3166`, `geo_sea_ocean`, `geo_loc_raw`.
  - Accepted input formats (in parsing order):
    - `"Country: Region, Locality"` -- canonical NCBI format
    - `"Country: Region"` -- colon, no comma
    - `"Country, Locality"` -- no colon, comma separates country and locality
    - `"Country"` -- bare name
    - Ocean/sea names -> `geo_sea_ocean` column
    - Decimal coordinates -> `geo_loc_raw`, country columns left empty
  - UK sub-country: England/Scotland/Wales/Northern Ireland -> `geo_country="United Kingdom"`, `geo_iso3166="GB"`.
  - Korea: bare "Korea" defaults to "KR" with a logged warning.
  - Taiwan: explicit lookup -> `geo_iso3166="TW"`.
  - **Bug fixes applied:**
    - `_split_geo_string`: comma-only fallback added -- inputs like `"Germany, Bavaria"` (no colon) now correctly parse country and locality instead of passing the full string to `search_fuzzy`.
    - `_resolve_iso`: short-string guard (`len < 3`) added before `search_fuzzy` to prevent phantom ISO code matches on strings like `"EU"` or `"NA"`.
    - `_resolve_iso`: exception catch broadened from `LookupError` to bare `Exception` to handle `AttributeError`/`TypeError` from some pycountry versions.
    - `geo_loc_raw`: now stores the original input value for all resolved records (previously always `NaN` in the normal resolution path), providing an audit trail.

- [x] **Module 5 (One Health):** `src/biometaharmonizer/one_health.py` -- COMPLETE.
  - `TIER1_PATTERNS` is a **tuple** of `(category, pattern)` pairs (not a dict) so priority order is structurally enforced.
  - Priority order: Environmental > Animal > Human > Food > Lab. Order is load-bearing.
  - Pattern specificity: "bovine blood" -> Animal; "environmental swab" -> Environmental.
  - `classify_joint(isolation_source_series, host_series)`: classifies isolation_source first, falls back to host using boolean indexing (not `.loc`) to avoid index mismatch `KeyError`.
  - `classify_with_confidence(series)`: returns DataFrame with `one_health_category`, `one_health_term`, `one_health_confidence` (1.0 for match, 0.0 for unclassified/missing).
  - **Bug fixes applied:**
    - Empty string after `.strip()` now returns `np.nan` (not `"Unclassified"`), consistent with NULL_PATTERNS behavior.
    - `classify_joint` result is `.copy()`-ed before boolean-mask assignment to prevent `SettingWithCopyWarning`.
    - `host_series.loc[fallback_mask]` replaced with `host_series[fallback_mask]` (boolean indexing) to avoid `KeyError` on index mismatch.

- [x] **Module 6 (Output):** `src/biometaharmonizer/output.py` -- COMPLETE.
  - `write(df, path, fmt="csv")` supports `csv`, `tsv`, `excel`, `parquet`. Format strings are case-insensitive.
  - `write_summary(df, path)` writes `column_name, non_null_count, fill_pct` CSV.
  - Parent directories created automatically.
  - **Bug fix applied:** `fmt = fmt.lower()` normalisation added so callers passing `"CSV"`, `"Parquet"`, etc. do not receive a spurious `ValueError`.

- [x] **Module 7 (CLI):** `src/biometaharmonizer/cli.py` -- COMPLETE.
  - Entrypoint: `biometaharmonizer run --input ids.txt --email user@email.com --output out.csv`
  - Supports all pipeline flags: `--api-key`, `--cache-dir`, `--model`, `--threshold`, `--drop-sparse`, `--no-drop-junk`, `--format`, `--summary`, `--skip-dates`, `--skip-geo`, `--skip-one-health`, `--verbose`.
  - Input accepts either a file path or a comma-separated accession string.
  - **Bug fixes applied:**
    - `--drop-sparse` type changed from `int` to `float` so fractional thresholds (e.g. `0.05`) pass through correctly to `_drop_sparse_columns`.
    - `df.join(geo_df)` replaced with a collision-safe pattern that only joins columns not already present in `df`, preventing `ValueError` on duplicate column names.

### 4. Schema Architecture (current)
- **`schemas/unified.json`** -- LEGACY (superseded by `ncbi_attributes.xml` in v0.3.0). Not loaded by KeyMapper.
- **`schemas/mandatory_fields.json`** -- Maps all 22 `ncbi_package` values to required fields. Includes `default` fallback. `lat_lon` added to all MIGS.ba.* and MIMS.me.* packages. `host` added to MIMS.me.human-gut.6.0.
- **`schemas/pathogen_cl_1.0.json`** -- Legacy. Not loaded by KeyMapper.
- **`schemas/pathogen_env_1.0.json`** -- Legacy. Not loaded by KeyMapper.
- **`schemas/ncbi_attributes.xml`** -- NCBI official attribute harmonization table, fetched by `scripts/build_ncbi_attribute_cache.py`.
- **`schemas/ncbi_embeddings.npy`** -- Precomputed embeddings, shape [N, dim], float32. Model recorded in `ncbi_cache_meta.json`.
- **`schemas/ncbi_harmonized_names.json`** -- Sorted list of N harmonized names; row index matches `ncbi_embeddings.npy`.
- **`schemas/ncbi_cache_meta.json`** -- Build metadata: model name, embedding dim, count, timestamp.

### 5. KeyMapper Configuration
```python
# Default: model read from ncbi_cache_meta.json (all-MiniLM-L6-v2 unless rebuilt)
mapper = KeyMapper()

# Custom model (rebuild the cache with the same model first)
mapper = KeyMapper(model="BAAI/bge-small-en-v1.5", threshold=0.70)

# Fractional fill-rate threshold (drop columns with < 5% non-null values)
df = mapper.map_columns(df, drop_sparse=0.05)

# Absolute row count threshold (drop columns with < 10 non-null values)
df = mapper.map_columns(df, drop_sparse=10)
```

### 6. Test Infrastructure
- `conftest.py` at repo root injects `src/` into `sys.path` for pytest.
- `tests/conftest.py` registers the `network` marker for live NCBI tests.
- Run all tests: `pytest tests/ -v --tb=short`
- Run with coverage: `pytest tests/ -v --cov=biometaharmonizer --cov-report=term-missing`
- Run without network tests: `pytest tests/ -v -m "not network"`
- Current: **170/170 passing**. All tests are self-contained (no live NCBI calls unless `network` marker).
- `test_key_mapper.py`: covers `drop_sparse` (int and float), `drop_junk`, `_PROTECTED_COLUMNS`, per-package mandatory validation, exact-synonym and semantic resolution, RuntimeError on missing cache.
- `test_output.py`: covers `write()` (csv/tsv/parquet/excel/invalid/case-insensitive fmt), parent dir creation, return type, `write_summary()` fill rates.
- `test_pipeline.py`: end-to-end integration -- KeyMapper rename, DateEngine, GeoEngine (snake_case columns, safe join), OneHealthClassifier, output write.
- `test_date_engine.py`: ISO 8601 truncated output, INSDC range parsing, check order (INSDC before two-digit guard), null guard, warning logs on malformed month.
- `test_geo_engine.py`: snake_case columns, UK sub-country, Korea ambiguity, Taiwan, ocean/sea, coordinate detection, comma-only format, short-string ISO guard, geo_loc_raw audit.
- `test_one_health.py`: TIER1_PATTERNS tuple iteration, pattern specificity (bovine blood, environmental swab), empty string -> NaN, `classify_joint()` index-safety, `classify_with_confidence()`.
- `test_ingestion.py`: load/classify/parse/ingest mocked tests, deduplication, network marker.

### 7. Colab Setup (every session)
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

os.chdir("/content")

import biometaharmonizer
print(biometaharmonizer.__version__)  # should print 0.3.0
```

### 8. Next Steps (in priority order)
1. **Run `build_ncbi_attribute_cache.py` on real NCBI data** and validate key harmonization fill rate improvement vs. `unified.json` baseline on a representative dataset.
2. **Manuscript benchmarking** -- parse rates per field before/after Option C (baseline = `unified.json`, test = `ncbi_attributes.xml` + embeddings). The delta demonstrates the value of the two-layer approach.
3. **PyPI release** -- finalize `MANIFEST.in`, test twine upload to TestPyPI, publish to PyPI.
4. **Update test suite** to cover all third-pass bug fixes (geo comma-only fallback, one_health tuple iteration, output case-insensitive fmt, date engine warning logs).

### 9. Initialization Command
Please acknowledge receipt of this protocol. Then summarize the current architectural approach in one sentence and state what the next immediate task is.
