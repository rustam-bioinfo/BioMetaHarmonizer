.. _changelog:

=========
Changelog
=========

All notable changes to BioMetaHarmonizer are documented in this file.
Format follows `Keep a Changelog <https://keepachangelog.com/en/1.0.0/>`_.

----

v1.0.0 — 2026-05-12
---------------------

First stable release. All known bugs and robustness issues have been resolved
before this milestone.

Fixed
~~~~~

- **[CRIT] Bare ``except Exception`` in ``_fetch_batch_with_retry()``** swallowed
  programming errors and retried non-transient failures (``MemoryError``,
  ``AttributeError``). Replaced with a typed ``_TRANSIENT_EXCEPTIONS`` tuple so
  only genuine network errors are retried.

- **[CRIT] ``ingest()`` raised an uninformative ``ValueError``** when all
  accessions were unresolvable. Error message now includes a per-reason
  breakdown (unrecognised IDs, stale assembly cache).

- **[CRIT] Test suite claimed 7 test files; only 1 existed.** Added
  ``test_date_engine.py``, ``test_geo_engine.py``, ``test_one_health.py``,
  ``test_key_mapper.py``, ``test_output.py``, and ``test_pipeline.py`` with
  full synthetic-data coverage and no live NCBI calls.

- **[HIGH] ``_resolve_gcx_via_entrez()`` silently discarded unresolved GCF
  accessions** after failed ``esummary`` retries, making the returned
  ``unresolved`` list shorter than the true count.

- **[HIGH] Double-logging of ``ET.ParseError``** — error was logged inside
  ``_parse_biosample_xml`` and again in both callers. Now handled once inside
  the parser; callers carry no ``ET.ParseError`` blocks.

- **[HIGH] ``--fetch-batch-size`` had no upper bound**; values above the
  NCBI-recommended 500 now trigger a warning and are clamped to 500.

- **[HIGH] ``--format`` CLI argument rejected uppercase inputs** despite
  README claiming case-insensitivity. A custom ``type=`` function now
  normalises the value at parse time.

- **[HIGH] ``_resolve_biosample_to_assembly()`` used ``iterrows()``** on a
  2 M-row DataFrame. Replaced with vectorised ``groupby`` for a ~4x speedup.

- **[HIGH] ``ET.ParseError`` on empty/malformed NCBI XML** crashed the entire
  batch. ``_parse_biosample_xml`` now guards against empty responses before
  calling ``ET.fromstring``.

- **[HIGH] No rate-limit sleep between ``esearch`` and ``esummary``** in
  ``_resolve_accessions_to_uids()``. A sleep is now added after each inner
  ``esummary`` call.

- **[HIGH] ``set_email()`` accepted any string without validation.**
  Lightweight RFC-5321 format check added; known placeholder addresses
  (``your@email.com``) are explicitly rejected.

- **[HIGH] README column-count errors** — stated 57 columns and "5 new from
  OneHealthClassifier"; corrected to 58 columns and 6 new columns.

- **[MED] Non-atomic output write** in ``output.py`` left corrupt partial files
  on interrupted writes. Now uses a temp-file-then-rename (atomic on POSIX)
  pattern.

- **[MED] ``_schemas_dir()`` duplicated** in ``ingestion.py`` and
  ``synonyms.py``. Removed local copy from ``ingestion.py``; both modules now
  import from ``synonyms.py``.

- **[MED] ``DateEngine.NULL_PATTERNS`` incomplete** relative to
  ``ingestion._NULL_PATTERNS``. Shared ``NULL_PATTERNS`` regex extracted to
  ``constants.py`` and imported by both modules.

- **[MED] ``OneHealthClassifier`` lacked deduplication cache** in ``classify()``
  and ``classify_with_confidence()``. Both methods now pre-compute results for
  unique values only, matching the behaviour of ``DateEngine`` and
  ``GeoEngine``.

- **[MED] ``_OCEAN_SEA`` set missing 12+ common marine bodies** present in
  NCBI submissions (Persian Gulf, Gulf of Mexico, Bay of Bengal, etc.).
  Set expanded to 35 named water bodies with Gulf/Bay/Sea prefix matching.

- **[MED] HTTP 429 not distinguished from other transient errors.** NCBI
  rate-limit responses now trigger adaptive backoff (up to 60 s) and a
  targeted warning suggesting ``--api-key``.

- **[MED] ``generate_summary_report.py`` was completely undocumented.** Module
  docstring and a ``Scripts`` section in the README added.

- **[MED] No dependency upper bounds** allowed potentially breaking installs
  with pandas 3.x or biopython 2.x. Conservative upper bounds added to all
  dependencies in ``pyproject.toml``.

- **[MED] ``OneHealthClassifier`` Lab detection threshold** was incorrect for
  multi-digit culture collection numbers (e.g. ``ATCC 25922``). Fixed with
  ``_COLLECTION_NUMBER_RE`` regex.

- **[MED] ``one_health_dictionaries.json`` not validated at load time.**
  ``_load_dictionaries()`` now checks all required top-level keys immediately
  after ``json.load()`` and raises a descriptive ``ValueError`` on failure.

- **[MED] ``_parse_biosample_xml()`` silently dropped attributes with empty
  string values**, bypassing ``_normalize_null()``. All attributes are now
  always written through the null-normalisation path.

- **[MED] ``ingest()`` did not validate that email was set** before NCBI API
  calls. An explicit ``ValueError`` is now raised if no email is configured.

- **[MED] Root ``conftest.py`` manually injected ``src/`` into ``sys.path``**,
  conflicting with the ``pyproject.toml`` src-layout install. Removed;
  ``[tool.pytest.ini_options] pythonpath = ["src"]`` is now the canonical
  mechanism.

- **[LOW] ``_DEFAULT_EMAIL`` sentinel passed email validation**, allowing
  placeholder addresses to reach NCBI. Blocklist of known placeholder addresses
  added to ``set_email()``.

- **[LOW] ``write()`` in ``output.py`` raised ``FileNotFoundError``** on nested
  output paths. ``path.parent.mkdir(parents=True, exist_ok=True)`` added.

- **[LOW] Korea ambiguity warning fired at ``WARNING`` level** for every unique
  value, flooding logs for Korean-heavy datasets. Downgraded to ``INFO``.

- **[LOW] ``ingest([])`` returned an empty DataFrame with no warning.**
  An informative ``logger.warning()`` is now emitted for empty input.

Performance
~~~~~~~~~~~

- ``GeoEngine.parse()`` now deduplicates unique ``geo_loc_name`` values before
  parsing, reducing ``pycountry.search_fuzzy()`` calls by 50–100x.

- ``OneHealthClassifier._classify_text()`` results are cached per-instance;
  ``classify_multi_field()`` pre-warms the cache from unique values across all
  fields, reducing calls from ~700 k to ~5–10 k for a typical 117 k-row dataset.

- ``_normalize_synonyms()`` replaced N sequential ``re.sub()`` calls with a
  single combined alternation regex, reducing synonym normalization from O(N)
  to O(1) regex calls per value.

- ``DateEngine.parse()`` and ``parse_with_range()`` deduplicate before
  invoking ``dateutil``, giving up to ~780x fewer ``dateutil.parse()`` calls.

- ``_resolve_biosample_to_assembly()`` replaced ``iterrows()`` with vectorised
  pandas operations (~4x speedup on 2 M-row assembly index).

- ``_read_assembly_summary()`` results are now cached via
  ``functools.lru_cache`` keyed on path and ``mtime``, eliminating redundant
  3–15 s disk reads in multi-call sessions.

- Switched ``esearch`` bulk UID resolution to the NCBI History server
  (``epost`` + ``esummary`` via ``WebEnv``/``query_key``), eliminating long
  OR-term query strings and reducing round-trips for large accession sets.

- Added randomised jitter to all inter-request sleeps and a pre-batch warm-up
  sleep to reduce NCBI connection resets for unauthenticated users.

Changed
~~~~~~~

- Logging format updated to include timestamps (``%H:%M:%S``) and
  left-aligned level names for aligned columns. Per-step elapsed time is
  logged at ``INFO`` level.

- Collision warnings in ``build_dictionaries.py`` now emit one grouped summary
  line per conflict pattern instead of one line per term; individual terms
  remain visible at ``DEBUG`` level.

- ``setup.py`` removed; ``pyproject.toml`` with dynamic versioning is the sole
  build entry point.

- Antibiogram XML is now parsed into a native list in
  ``_extra_attributes["antibiogram"]`` rather than a double-serialised JSON
  string.

- NCBI esearch ``'Remote end closed connection'`` improved log message now
  explicitly identifies the likely cause as rate limiting and suggests
  registering an API key.

----

v0.6.0 — 2025
---------------

Added
~~~~~

- **Fixed 57-column output schema** defined in ``_load_final_schema()``
  (``ingestion.py``). Every record is pre-initialised with all 57 columns so
  downstream code never needs to handle missing columns.

- **GeoEngine** (``geo_engine.py``) — structured parsing of ``geo_loc_name``
  strings into five output columns: ``geo_country``, ``geo_region``,
  ``geo_locality``, ``geo_iso3166``, ``geo_sea_ocean``.
  Includes ISO 3166-1 resolution via ``pycountry``, UK sub-country handling,
  country alias table, historical country detection, and ocean/sea lookup.

- **DateEngine** (``date_engine.py``) — ISO 8601 truncated date parsing with
  seven range-detection patterns applied before ``dateutil`` to prevent silent
  misparsing. Populates two output columns: ``collection_date`` (point date)
  and ``collection_date_range`` (verbatim original for ranges/approximate).

- **OneHealthClassifier** (``one_health.py``) — multi-layer, multi-field One
  Health categorization loaded from ``one_health_dictionaries.json``. Supports
  ``classify()``, ``classify_joint()``, ``classify_with_confidence()``, and
  ``classify_multi_field()`` methods. Confidence model with ``high``,
  ``medium``, ``low``, and ``unresolved`` evidence levels. Optional
  ``rapidfuzz`` fuzzy fallback layer. Valid output categories: ``Human``,
  ``Animal``, ``Plant``, ``Food``, ``Environmental``, ``Unclassified``.

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
  functions supporting CSV, TSV, Excel (openpyxl), Parquet (pyarrow), and
  JSONL output formats.

- **CLI** (``cli.py``) — ``biometaharmonizer run`` subcommand with full
  pipeline: ingest -> key-map -> date/geo/One Health -> output. Supports
  one or more simultaneous output formats (``--format csv tsv excel jsonl``),
  format auto-inference from file extension, comma-separated accession input,
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

- **Removed ``Aquatic``, ``Wildlife``, and ``Lab`` One Health categories.**
  These values are no longer emitted by ``OneHealthClassifier``. Lab signals
  are captured in the ``one_health_processing`` and ``one_health_setting``
  columns instead. Aquatic and Wildlife signals are subsumed by
  ``Environmental`` and ``Animal`` respectively.

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
