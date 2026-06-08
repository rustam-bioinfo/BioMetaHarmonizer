# Changelog

All notable changes to BioMetaHarmonizer will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---
v1.0.0 — 2026-05-12
---------------------

First stable release. All known bugs and robustness issues have been resolved
before this milestone.

Highlights
30+ bug fixes across ingestion, date parsing, geo parsing, and One Health classification
Major performance improvements: up to 780x fewer dateutil calls, 50–100x fewer pycountry lookups, ~4x faster assembly index resolution
Full test suite added (7 test files, synthetic data only — no live NCBI calls)
pyproject.toml promoted to Development Status :: 5 - Production/Stable
Atomic output writes, HTTP 429 adaptive backoff, and email validation added
