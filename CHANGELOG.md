# Changelog

All notable changes to BioMetaHarmonizer will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Fixed
- **MED-7** `ingestion.py`: HTTP 429 (NCBI rate limit) now triggers dedicated adaptive backoff
  (`_HTTP_429_WAIT_S = 60 s`) instead of short exponential delay.
- **MED-2** `ingestion.py`: Removed duplicate `_schemas_dir()` — now delegated to the single
  canonical implementation in `synonyms.py`.
- **MED-10** `one_health.py`: Lab detection for culture collection numbers (e.g. `ATCC 25922`)
  now uses `_COLLECTION_NUMBER_RE` regex instead of a brittle `< 4 char` length threshold.
- **MED-9** `pyproject.toml`: Added conservative upper bounds on all dependencies
  (`pandas<3.0`, `biopython<2.0`, `requests<3.0`, `python-dateutil<3.0`,
  `openpyxl<4.0`, `pyarrow<20.0`, `rapidfuzz<4.0`) to prevent silent breakage
  from major-version upgrades.
- **LOW-8** `geo_engine.py`: `_PAREN_RE` changed from greedy `.*` to negated char class
  `[^)]*` — strips only the last parenthetical group, not the entire span.
- **LOW-6** `ingestion.py`: Exponential backoff no longer sleeps on the last failed retry
  attempt before giving up — eliminates up to 8 s of wasted delay per failed batch.
- **LOW-3** `ingestion.py`: Duplicate attributes that resolve to the same standard key are
  now stored under `_dup_<standard_key>` in `_extra_attributes` for predictable lookup.

### Added
- **MED-5** `geo_engine.py`: Expanded `_OCEAN_SEA` set from 17 to 45+ named water bodies
  (gulfs, bays, straits, additional seas) to reduce marine sample fallthrough to country
  resolver.
- **MED-4** `one_health.py`: Per-instance `lru_cache(maxsize=4096)` on `_classify_text`
  eliminates redundant pattern matching for repeated field values across large datasets.
- **LOW-5** `CHANGELOG.md`: Added this file following Keep a Changelog convention.
- **LOW-4** `pyproject.toml`: Added `authors` field with contact email for PyPI/citation.

### Changed
- **MED-6** `ingestion.py`: Thread-safety warning added to `ingest()` docstring and module
  header — module-level globals are not safe for concurrent use from multiple threads.

---

## [0.6.0] - 2026-04-01

### Added
- Two-layer synonym lookup (`synonyms.py`): unified.json + optional NCBI attribute XML.
- Shared `build_synonym_lookup()` used by both `ingestion.py` and `key_mapper.py`.
- Antibiogram parsing for NCBI pathogen package XML (`<Antibiogram>` table).
- `OneHealthClassifier.classify_multi_field()` — six-field two-pass evidence integration.
- Confidence scoring (`one_health_confidence`, `one_health_evidence_level`).
- `one_health_processing` and `one_health_setting` output columns.
- Assembly accession resolution via local index + Entrez elink fallback.
- `refresh_cache` flag to bypass 7-day assembly summary TTL.
- Atomic output writes in `output.py` (temp-file-then-rename).
- CLI `--format` now case-insensitive via `_lower_format()` type function.
- `collected_by` priority: explicit BioSample attribute preferred over `<Owner/Name>`.
- `geo_sea_ocean` and `geo_loc_raw` output columns in `GeoEngine`.
- Expanded null normalization patterns (30+ variants including misspellings).
- `--fetch-batch-size` clamped to NCBI-recommended maximum of 500.

### Fixed
- Double-logging of `ET.ParseError` — now handled entirely inside `_parse_biosample_xml`.
- `map_columns()` emits warning before `reindex()` drops extra columns.
- `set_email()` rejects known placeholder email addresses.

---

## [0.5.0] - 2026-01-15

### Added
- Initial public release.
- `ingest()`, `KeyMapper`, `DateEngine`, `GeoEngine`, `OneHealthClassifier`.
- CSV, TSV, Excel, Parquet output formats.
- Assembly summary flat-file cache with 7-day TTL.
