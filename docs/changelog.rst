.. _changelog:

=========
Changelog
=========

All notable changes to BioMetaHarmonizer are documented in this file.
Format follows `Keep a Changelog <https://keepachangelog.com/en/1.0.0/>`_.

----

v0.6.0 — 2025
---------------

Added
~~~~~

- **Fixed 51-column output schema** defined in ``_load_final_schema()``
  (``ingestion.py``). Every record is pre-initialised with all 51 columns so
  downstream code never needs to handle missing columns.

- **GeoEngine** (``geo_engine.py``) — structured parsing of ``geo_loc_name``
  strings into six output columns: ``geo_country``, ``geo_region``,
  ``geo_locality``, ``geo_iso3166``, ``geo_sea_ocean``, ``geo_loc_raw``.
  Includes ISO 3166-1 resolution via ``pycountry``, UK sub-country handling,
  country alias table, historical country detection, ocean/sea lookup, and
  coordinate-only entry detection.

- **DateEngine** (``date_engine.py``) — ISO 8601 truncated date parsing with
  seven range-detection patterns applied before ``dateutil`` to prevent silent
  misparsing. Populates two output columns: ``collection_date`` (point date)
  and ``collection_date_range`` (verbatim original for ranges/approximate).

- **OneHealthClassifier** (``one_health.py``) — multi-layer, multi-field One
  Health categorization loaded from ``one_health_dictionaries.json``. Supports
  ``classify()``, ``classify_joint()``, ``classify_with_confidence()``, and
  ``classify_multi_field()`` methods. Confidence model with ``high``,
  ``medium``, ``low``, and ``unresolved`` evidence levels. Optional
  ``rapidfuzz`` fuzzy fallback layer.

- **Antibiogram extraction** (``_parse_antibiogram()`` in ``ingestion.py``) —
  automatic parsing of ``<Table class="Antibiogram.1.0">`` XML tables from
  NCBI Pathogen BioSample packages. Ten canonical field names via
  ``_ANTIBIOGRAM_HEADER_MAP``. Result stored as a native list in
  ``_extra_attributes["antibiogram"]``.

- **Assembly accession support** (``ingestion.py``) — GCF\_/GCA\_ accessions
  are resolved to BioSample accessions via a two-step process: local NCBI
  assembly summary index (auto-downloaded, 7-day TTL) followed by an Entrez
  elink fallback.

- **``_extra_attributes`` column** — all BioSample attributes that do not
  map to a named schema column are preserved as a JSON dict. Multiple values
  for the same key are pipe-joined.

- **Back-fill from assembly index** — ``bioproject_accession``,
  ``assembly_accession_refseq``, and ``assembly_accession_genbank`` are
  back-filled for all records whose BioSample accession appears in the
  cached assembly summary files.

- **Two-layer synonym lookup** (``synonyms.py``) — ``unified.json`` (Layer 1)
  plus optional ``ncbi_attributes.xml`` (Layer 2, built by
  ``scripts/build_ncbi_attribute_cache.py``). Result cached via
  ``functools.lru_cache``.

- **KeyMapper** (``key_mapper.py``) — column renaming and coalescing for
  custom/non-ingestion workflows using the shared synonym lookup.

- **Output module** (``output.py``) — ``write()`` and ``write_summary()``
  functions supporting CSV, TSV, Excel (openpyxl), and Parquet (pyarrow).

- **CLI** (``cli.py``) — ``biometaharmonizer run`` subcommand with full
  pipeline: ingest → key-map → date/geo/One Health → output. Supports format
  auto-inference from file extension, comma-separated accession input,
  ``--summary`` fill-rate output, ``--refresh-cache`` flag.

- **``build_dictionaries.py`` script** — builds ``one_health_dictionaries.json``
  from OLS4 (ENVO, FoodOn, UBERON, Plant Ontology), NCBI Taxonomy BFS walk,
  and optional UMLS synonym expansion. Implements ``base_wins`` merge strategy
  and ``_resolve_collisions()`` with ``ambiguous_category_terms`` output.

- **``build_ncbi_attribute_cache.py`` script** — fetches
  ``ncbi_attributes.xml`` from NCBI for Layer 2 synonym coverage.

- **``generate_summary_report.py`` script** — generates interactive HTML
  reports with Plotly visualizations covering data quality, geography, temporal
  trends, One Health distribution, host analysis, and ``_extra_attributes``
  coverage.

- **``refresh_cache``** parameter on ``ingest()`` and ``--refresh-cache`` CLI
  flag for forcing re-download of assembly summary files.

- **Exponential backoff retry** — ``_MAX_RETRIES = 3``, base 2 s, capped at
  30 s, applied to all transient Entrez request failures.

- **Null normalization** — comprehensive ``_NULL_PATTERNS`` regex covering
  30+ explicit null/missing/restricted/unknown variants applied to every
  parsed attribute value.

- **Rate-aware inter-batch sleep** — ``0.12 s`` with API key, ``0.34 s``
  without, computed from the module-level ``ENTREZ_API_KEY`` value.

Changed
~~~~~~~

- Switched from per-module synonym tables to a single shared
  ``build_synonym_lookup()`` function in ``synonyms.py`` consumed by both
  ``ingestion.py`` and ``key_mapper.py``.

- ``KeyMapper.map_columns()`` no longer drops columns; all overflow attributes
  are preserved in ``_extra_attributes`` by the ingestion layer.

- Assembly summary files are now read with ``functools.lru_cache`` keyed on
  path and ``mtime`` to avoid redundant disk reads within a session.

Fixed
~~~~~

- Cross-batch ``WebEnv``/``query_key`` accumulation bug: each ``esearch``
  batch now creates its own fresh History slot so that ``efetch`` retrieves
  exactly the records in that batch.

- ``2018-2020``-style year ranges are no longer silently misparsed as
  ``2018-01-20`` by ``dateutil``; they are caught by ``_YEAR_ONLY_RANGE``
  before ``dateutil`` is invoked.

- Country strings containing a parenthesised qualifier with an internal comma
  (e.g. ``"United Kingdom (England, Wales & N. Ireland)"``) are no longer
  incorrectly split at the internal comma.
