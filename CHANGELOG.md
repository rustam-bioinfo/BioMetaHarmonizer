# Changelog

All notable changes to BioMetaHarmonizer will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.9.0] - 2026-10-22

### Added

- Optional metadata post-processing before export.
- Decimal latitude and longitude parsing from combined coordinate strings
  and separate coordinate attributes retained in `_extra_attributes`.
- Recovery of submitted host health-state metadata without inferring a diagnosis.
- Standards-oriented aliases: `seq_meth`, `assembly_software`,
  `host_disease_stat`, `country`, `countryCode`, `waterBody`, and `locality`.
- Conservative offline inference of missing derived country fields using
  a bundled Natural Earth polygon reference.
- Separate row-level diagnostics for coordinate parsing, conflicting sources,
  country lookup, assignment provenance, and coordinate-datum assumptions.
- Optional geographic dependencies through the `geo` extra.
- Checksum-verified loading and caching of the bundled country reference.

### Changed

- Collection-date parsing now preserves supported explicit clock-time precision,
  fractional seconds, and resolved timezone offsets.
- Supported timestamp intervals are retained in `collection_date_range`
  rather than converted to point dates.
- Simplified the CLI by removing the redundant post-ingestion column-mapping
  stage. Attribute-name resolution already occurred during ingestion in
  previous versions.
- Optional post-processing extends canonical output from 53 to 63 columns
  while preserving native fields and One Health predictions.

### Removed

- The standalone `biometaharmonizer.key_mapper` module.
- The package-level `KeyMapper` export.

### Limitations

- Coordinate-derived country assignment fills only the derived `country`
  and `countryCode` pair when both fields are absent.
- Existing text assignments and historical or special annotations are retained.
- Missing sample datum information is recorded as an assumption of WGS84.
  Unsupported explicit datums are not transformed.
- Boundary points, ambiguous overlaps, unmapped reference units, and points
  outside the polygon layer remain unresolved.
- Country inference does not determine marine jurisdiction or legal boundaries.
- Standards-oriented aliases do not constitute full MIxS or Darwin Core compliance.


## [1.0.0] - 2026-05-12

First stable release. All known bugs and robustness issues have been resolved before this milestone.

### Added
* Full test suite (7 test files, synthetic data only, no live NCBI calls).
* Atomic output writes, HTTP 429 adaptive backoff, and email validation.

### Changed
* Major performance improvements: up to 780x fewer dateutil calls, 50 to 100x fewer pycountry lookups, and ~4x faster assembly index resolution.
* Promoted `pyproject.toml` status to `Development Status :: 5 - Production/Stable`.

### Fixed
* Over 30 bug fixes across ingestion, date parsing, geo parsing, and One Health classification.
##
## [1.0.1] - 2026-06-09

### Added
* Extended synonym coverage for `isolation_source`
* Extended synonym coverage for `collection_date`
* Extended synonym coverage for `geo_loc_name`
* Extended synonym coverage for `host`

### Fixed
* Reduced volume of attributes incorrectly landing in `_extra_attributes` due to
  missing synonym variants for core fields.

##
## [1.0.3] - 2026-06-15

### New Features

- `build-dicts`, `build-ncbi-cache`, and `generate-report` are now available as
  `biometaharmonizer` subcommands — no longer require running standalone scripts directly
- `biometaharmonizer --help` now lists all four subcommands:
  `run`, `build-dicts`, `build-ncbi-cache`, `generate-report`

### Changes

- `scripts/build_dictionaries.py`, `scripts/build_ncbi_attribute_cache.py`,
  `scripts/generate_summary_report.py` — `parse_args()` now accepts an optional
  `argv` parameter; scripts remain usable standalone
- `cli.py` — added subparsers and dispatch logic for the three new subcommands
