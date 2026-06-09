# Changelog

All notable changes to BioMetaHarmonizer will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
