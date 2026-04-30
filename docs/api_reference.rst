.. _api_reference:

=============
API Reference
=============

This page documents every public function and class exported by
BioMetaHarmonizer v0.6.0. Parameter names, defaults, and types are
derived directly from the source code.

biometaharmonizer.ingestion
-----------------------------

.. function:: biometaharmonizer.ingestion.ingest(source, email=None, api_key=None, cache_dir=None, fetch_batch_size=None, esearch_batch_size=None, refresh_cache=False) -> pd.DataFrame

   Fetch and return harmonized BioSample metadata as a DataFrame.

   Accepts BioSample accessions (``SAMN``/``SAME``/``SAMD``), assembly
   accessions (``GCF_``/``GCA_``), or a mix. Assembly accessions are
   resolved to BioSample accessions via a local assembly index (downloaded
   once to ``cache_dir``) with an Entrez elink fallback. BioSample records
   are fetched using the ``esearch(usehistory="y")`` + ``efetch`` pipeline
   in batches of ``fetch_batch_size``.

   :param source: Path to a file of accessions (one per line), a list of
       accession strings, or a single accession string. Accepted prefixes:
       ``SAMN``, ``SAME``, ``SAMD`` (BioSample) or ``GCF_``, ``GCA_``
       (assembly).
   :type source: str, Path, or list[str]
   :param email: Contact email for NCBI Entrez. Required if not previously
       set via :func:`set_email`. Must match ``^[^@\s]+@[^@\s]+\.[^@\s]+$``.
   :type email: str or None
   :param api_key: NCBI API key. Raises rate limit from 3 to 10 req/s.
       Optional; if not provided the module-level ``ENTREZ_API_KEY`` is used.
   :type api_key: str or None
   :param cache_dir: Directory for assembly summary flat-file cache.
       Defaults to ``~/.biometaharmonizer/cache/``.
   :type cache_dir: str, Path, or None
   :param fetch_batch_size: Records per ``efetch`` request. Defaults to 200.
       Higher values reduce round trips but increase per-request payload.
   :type fetch_batch_size: int or None
   :param esearch_batch_size: Accessions per ``esearch`` term (BioSample
       and elink paths). Defaults to 100.
   :type esearch_batch_size: int or None
   :param refresh_cache: When ``True``, delete and re-download the assembly
       summary files unconditionally. Defaults to ``False``.
   :type refresh_cache: bool
   :returns: DataFrame conforming to the 51-column fixed schema defined by
       ``_load_final_schema()``. Every column is initialized to ``None``/NaN
       for records that lack the attribute.
   :rtype: pandas.DataFrame
   :raises ValueError: If no valid email is provided (neither via ``email``
       parameter nor via prior :func:`set_email` call).

   .. code-block:: python

      import biometaharmonizer as bmh

      df = bmh.ingest(
          source=["SAMN02436525", "SAMN02434874"],
          email="your@email.com",
          api_key="YOUR_KEY",
          fetch_batch_size=500,
      )


.. function:: biometaharmonizer.ingestion.set_email(email: str) -> None

   Set the module-level Entrez contact email used by all subsequent
   ``ingest()`` calls.

   :param email: Valid e-mail address. Validated against the pattern
       ``^[^@\s]+@[^@\s]+\.[^@\s]+$``.
   :type email: str
   :raises ValueError: If ``email`` does not match the pattern.

   .. code-block:: python

      import biometaharmonizer as bmh
      bmh.set_email("researcher@institution.edu")


.. function:: biometaharmonizer.ingestion.set_api_key(key: str) -> None

   Set the module-level NCBI API key used by all subsequent ``ingest()``
   calls. Also sets ``Bio.Entrez.api_key``.

   :param key: NCBI API key string from
       https://www.ncbi.nlm.nih.gov/account/
   :type key: str

   .. code-block:: python

      import biometaharmonizer as bmh
      bmh.set_api_key("abc123def456...")


.. function:: biometaharmonizer.ingestion.set_cache_dir(path) -> None

   Override the directory used for assembly summary flat-file caching.
   Clears the internal ``_read_assembly_summary_cached`` LRU cache so
   that subsequent calls use the new path.

   :param path: New cache directory path.
   :type path: str or Path

   .. code-block:: python

      import biometaharmonizer as bmh
      bmh.set_cache_dir("/content/bmh_cache")   # Google Colab example


biometaharmonizer.output
--------------------------

.. function:: biometaharmonizer.output.write(df: pd.DataFrame, path, fmt: str = "csv") -> Path

   Write a harmonized DataFrame to disk in the specified format.

   :param df: Harmonized DataFrame to write.
   :type df: pandas.DataFrame
   :param path: Destination file path. Parent directories are created
       automatically.
   :type path: str or Path
   :param fmt: Output format. One of ``"csv"``, ``"tsv"``, ``"excel"``,
       ``"parquet"``. Case-insensitive. Defaults to ``"csv"``.
   :type fmt: str
   :returns: Resolved absolute path to the written file.
   :rtype: pathlib.Path
   :raises ValueError: If ``fmt`` is not one of the four supported formats.

   .. code-block:: python

      from biometaharmonizer.output import write
      write(df, "harmonized.parquet", fmt="parquet")


.. function:: biometaharmonizer.output.write_summary(df: pd.DataFrame, path) -> Path

   Write a fill-rate summary CSV for each column in ``df``.

   The output CSV has three columns: ``column_name``, ``non_null_count``,
   ``fill_pct``. ``fill_pct`` is rounded to one decimal place.

   :param df: Source DataFrame to summarize.
   :type df: pandas.DataFrame
   :param path: Destination file path for the summary CSV.
   :type path: str or Path
   :returns: Resolved absolute path to the written summary file.
   :rtype: pathlib.Path

   .. code-block:: python

      from biometaharmonizer.output import write_summary
      write_summary(df, "fill_rates.csv")


biometaharmonizer.date_engine
-------------------------------

.. class:: biometaharmonizer.date_engine.DateEngine

   Temporal parsing engine. Converts date strings to ISO 8601 truncated
   representation.

   .. method:: parse(series) -> pd.Series

      Parse a Series of date strings to ISO 8601 point dates. Deduplicates
      unique values before parsing for performance.

      :param series: Series of raw date strings.
      :type series: pandas.Series or pandas.DataFrame
      :returns: Series of ISO 8601 strings (``YYYY``, ``YYYY-MM``, or
          ``YYYY-MM-DD``) with NaN for unparseable or null values.
      :rtype: pandas.Series

   .. method:: parse_with_range(series) -> pd.DataFrame

      Parse dates and return a two-column DataFrame.

      :param series: Series of raw date strings.
      :type series: pandas.Series
      :returns: DataFrame with columns ``collection_date`` (ISO 8601 point
          date or NaN) and ``collection_date_range`` (verbatim original
          string for any range/approximate input, else NaN).
      :rtype: pandas.DataFrame

   .. staticmethod:: _detect_range(value) -> bool

      Return ``True`` if *value* represents a date range or approximate date
      of any supported format. Must be called before ``dateutil`` to prevent
      silent misparsing.


biometaharmonizer.geo_engine
------------------------------

.. class:: biometaharmonizer.geo_engine.GeoEngine

   Geospatial resolution engine. Parses ``geo_loc_name`` strings into
   structured geographic fields.

   .. method:: parse(series) -> pd.DataFrame

      Parse a Series of ``geo_loc_name`` strings into a six-column DataFrame.

      :param series: Series of ``geo_loc_name`` strings.
      :type series: pandas.Series
      :returns: DataFrame with columns: ``geo_country``, ``geo_region``,
          ``geo_locality``, ``geo_iso3166``, ``geo_sea_ocean``,
          ``geo_loc_raw``.
      :rtype: pandas.DataFrame

   .. code-block:: python

      from biometaharmonizer.geo_engine import GeoEngine
      import pandas as pd

      geo = GeoEngine()
      series = pd.Series(["Russia: Novosibirsk", "USA: California, San Diego"])
      result = geo.parse(series)
      print(result[["geo_country", "geo_region", "geo_iso3166"]])


biometaharmonizer.one_health
------------------------------

.. class:: biometaharmonizer.one_health.OneHealthClassifier

   One Health categorization classifier. Loads biological knowledge
   exclusively from ``one_health_dictionaries.json``.

   .. method:: classify(series) -> pd.Series

      Single-field classification from a Series of ``isolation_source``
      values.

      :param series: Series of isolation source strings.
      :type series: pandas.Series
      :returns: Series of One Health category strings.
      :rtype: pandas.Series

   .. method:: classify_joint(isolation_source_series, host_series) -> pd.Series

      Two-field classification using both ``isolation_source`` and ``host``.
      Both Series must share the same index.

      :param isolation_source_series: Series of isolation source strings.
      :type isolation_source_series: pandas.Series
      :param host_series: Series of host strings.
      :type host_series: pandas.Series
      :returns: Series of One Health category strings.
      :rtype: pandas.Series
      :raises ValueError: If the two Series do not share the same index.

   .. method:: classify_with_confidence(series) -> pd.DataFrame

      Single-field classification with confidence scores.

      :param series: Series of isolation source strings.
      :type series: pandas.Series
      :returns: DataFrame with columns: ``one_health_category``,
          ``one_health_term``, ``one_health_confidence``.
      :rtype: pandas.DataFrame

   .. method:: classify_multi_field(**fields) -> pd.DataFrame

      Multi-field evidence integration. Accepts named ``pd.Series`` for any
      of: ``isolation_source``, ``host``, ``env_medium``,
      ``env_local_scale``, ``env_broad_scale``, ``sample_type``.

      :returns: DataFrame with columns: ``one_health_category``,
          ``one_health_term``, ``one_health_confidence``,
          ``one_health_evidence_level``, ``one_health_processing``,
          ``one_health_setting``, ``one_health_source_field``.
      :rtype: pandas.DataFrame

   .. code-block:: python

      from biometaharmonizer.one_health import OneHealthClassifier
      import pandas as pd

      clf = OneHealthClassifier()
      sources = pd.Series(["blood", "chicken feces", "river sediment"])
      print(clf.classify(sources))
      # 0      Human
      # 1      Animal
      # 2      Environmental


biometaharmonizer.key_mapper
------------------------------

.. class:: biometaharmonizer.key_mapper.KeyMapper

   Harmonizes column names for custom or non-ingestion workflows by
   applying the two-layer synonym lookup from ``synonyms.py``.

   .. method:: map_columns(df) -> pd.DataFrame

      Rename raw columns to canonical standard keys, coalesce duplicate
      columns (using ``combine_first`` priority to the leftmost column),
      and reindex to the fixed output schema.

      :param df: Input DataFrame with potentially non-standard column names.
      :type df: pandas.DataFrame
      :returns: DataFrame reindexed to ``BIOSAMPLE_SCHEMA``.
      :rtype: pandas.DataFrame


biometaharmonizer.synonyms
----------------------------

.. function:: biometaharmonizer.synonyms.build_synonym_lookup() -> dict

   Build and return a ``{lowercased_synonym: standard_key}`` dict from two
   sources: ``unified.json`` (Layer 1) and ``ncbi_attributes.xml`` (Layer 2,
   optional). The result is cached via ``functools.lru_cache`` after the
   first call.

   :returns: Dictionary mapping lowercased synonym strings to canonical
       standard key strings. Returns an empty dict if neither schema file
       is present.
   :rtype: dict
