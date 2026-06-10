.. _harmonization:

=============
Harmonization
=============

After raw XML is fetched and parsed, four engines apply in-place transformations
to specific columns of the output DataFrame. All engines deduplicate unique
values before processing for performance.

Date Engine
-----------

Module: :mod:`biometaharmonizer.date_engine`
Class: :class:`~biometaharmonizer.date_engine.DateEngine`

The date engine converts any date string to ISO 8601 *truncated* representation
and populates two output columns:

- **``collection_date``** — ISO 8601 point date (``YYYY``, ``YYYY-MM``, or
  ``YYYY-MM-DD``). This field is **always** ``NaN`` for any range or approximate
  input — without exception.
- **``collection_date_range``** — the verbatim original string, set only for
  range/approximate inputs; ``NaN`` for all point-date inputs.

Range detection runs *before* ``dateutil.parser`` to prevent silent
misparsing. For example, ``2018-2020`` would be misparsed by dateutil as
``2018-01-20``, so it is caught by ``_YEAR_ONLY_RANGE`` first.

Range pattern evaluation order (first match wins):

1. ``_INSDC_SLASH_RANGE`` — numeric INSDC slash: ``2004-07/2004-12``
2. ``_YEAR_ONLY_RANGE`` — year-only range where start ≠ end: ``2018-2020``
3. ``_NUMERIC_DASH_RANGE`` — numeric dash or "to" word: ``2021-01-15 - 2021-03-20``
4. ``_NAMED_MONTH_SAME_YEAR`` — named-month same year: ``July-December 2004``
5. ``_NAMED_MONTH_CROSS_YEAR`` — named-month cross-year: ``Oct 2020-Feb 2021``
6. ``_SEASON_RANGE`` — season strings: ``Spring 2019``, ``Winter 2020-2021``
7. ``_APPROX_DATE`` — approximate prefixes: ``~2015``, ``circa 2010``,
   ``early March 2020``, ``late 2019``, ``mid-2018``

Bare two-digit year strings (e.g. ``"95"``) are always rejected and produce
a warning log.

The main public method is :meth:`~biometaharmonizer.date_engine.DateEngine.parse_with_range`,
which returns a two-column DataFrame. The legacy
:meth:`~biometaharmonizer.date_engine.DateEngine.parse` method returns only
``collection_date`` as a Series.

Date Parsing Examples
~~~~~~~~~~~~~~~~~~~~~

The following table shows representative inputs and their normalized outputs.

.. list-table:: Date parsing examples
   :header-rows: 1

   * - Input string
     - collection_date
     - collection_date_range
   * - ``2021``
     - ``2021``
     - NaN
   * - ``2021-06``
     - ``2021-06``
     - NaN
   * - ``2021-06-15``
     - ``2021-06-15``
     - NaN
   * - ``Jun 2019``
     - ``2019-06``
     - NaN
   * - ``15/06/2021``
     - ``2021-06-15``
     - NaN
   * - ``June 15, 2021``
     - ``2021-06-15``
     - NaN
   * - ``2018-2020``
     - NaN
     - ``2018-2020``
   * - ``2004-07/2004-12``
     - NaN
     - ``2004-07/2004-12``
   * - ``2021-01-15/2021-03-20``
     - NaN
     - ``2021-01-15/...``
   * - ``July-December 2004``
     - NaN
     - ``July-December 2004``
   * - ``Jan-Mar 2019``
     - NaN
     - ``Jan-Mar 2019``
   * - ``Oct 2020-Feb 2021``
     - NaN
     - ``Oct 2020-Feb 2021``
   * - ``Spring 2019``
     - NaN
     - ``Spring 2019``
   * - ``Winter 2020-2021``
     - NaN
     - ``Winter 2020-2021``
   * - ``~2015``
     - NaN
     - ``~2015``
   * - ``circa 2010``
     - NaN
     - ``circa 2010``
   * - ``early March 2020``
     - NaN
     - ``early March 2020``
   * - ``late 2019``
     - NaN
     - ``late 2019``
   * - ``missing``
     - NaN
     - NaN
   * - ``unknown``
     - NaN
     - NaN
   * - ``not provided``
     - NaN
     - NaN
   * - ``2015/2017``
     - NaN
     - ``2015/2017``

Geo Engine
----------

Module: :mod:`biometaharmonizer.geo_engine`
Class: :class:`~biometaharmonizer.geo_engine.GeoEngine`

The geo engine parses NCBI ``geo_loc_name`` strings into five structured output
columns. The expected input format is ``"Country: Region, Locality"``; the
fallback format is ``"Country, Locality"`` (no colon).

**Output columns populated:**

- ``geo_country`` — normalised country display name (e.g. ``"United Kingdom"``)
- ``geo_region`` — sub-national region as submitted (e.g. ``"England"``)
- ``geo_locality`` — locality or sub-region as submitted
- ``geo_iso3166`` — ISO 3166-1 alpha-2 country code (e.g. ``"GB"``), or the
  string ``"HISTORICAL"`` for defunct countries, or NaN if not resolvable
- ``geo_sea_ocean`` — ocean or sea name for marine samples (e.g. ``"Pacific Ocean"``)

The public method is :meth:`~biometaharmonizer.geo_engine.GeoEngine.parse`,
which accepts a :class:`pandas.Series` and returns a five-column
:class:`pandas.DataFrame`.

Special handling rules:

- **UK sub-countries:** ``"England"``, ``"Scotland"``, ``"Wales"``,
  ``"Northern Ireland"`` are all mapped to ISO code ``"GB"`` and display name
  ``"United Kingdom"``.
- **Country aliases:** ``"Turkey"``/``"Türkiye"`` → ``"TR"``;
  ``"Namibia"`` → ``"NA"``; ``"DR Congo"``/``"DRC"``/``"Congo-Kinshasa"`` → ``"CD"``;
  ``"Burma"``/``"Myanmar (Burma)"`` → ``"MM"``; ``"Palestine"``/``"Gaza"``/
  ``"West Bank"`` → ``"PS"``.
- **Historical countries:** ``"USSR"``, ``"Soviet Union"``, ``"Yugoslavia"``,
  ``"Czechoslovakia"``, ``"German Democratic Republic"``, ``"Zaire"``, and
  others are tagged ``geo_iso3166 = "HISTORICAL"`` and a WARNING is logged.
- **Coordinate-only entries:** values matching the coordinate pattern are not
  parsed into geo columns and return all-NaN geo outputs.
- **Parenthetical qualifiers in country names:** trailing parenthetical suffixes
  such as ``"United Kingdom (England, Wales & N. Ireland)"`` are stripped
  before country lookup so the comma inside the parentheses does not break
  parsing.
- **Parenthetical qualifiers in water body names:** ocean and sea names that
  include a regional qualifier in parentheses (e.g. ``"Pacific Ocean (NE)"``)
  have the parenthetical portion stripped before the water-body lookup, so the
  matched value stored in ``geo_sea_ocean`` is the canonical name without the
  qualifier (e.g. ``"Pacific Ocean"``).
- **Ocean/sea lookup:** when the country token (after stripping parenthetical
  qualifiers) matches one of the named ocean/sea entries, the value is
  stored in ``geo_sea_ocean`` instead of ``geo_country``.
- **Bare "Korea" ambiguity:** the string ``"Korea"`` without a North/South
  qualifier is resolved to **South Korea** (``geo_iso3166 = "KR"``) and an
  INFO-level log is emitted to flag the ambiguity:

  .. code-block:: text

     INFO  biometaharmonizer.geo_engine:
       'Korea' resolved to South Korea (KR) — verify if North Korea was intended.

Geo Parsing Examples
~~~~~~~~~~~~~~~~~~~~~

.. list-table:: Geographic string parsing examples
   :header-rows: 1
   :widths: 40 16 16 16 12

   * - Input string
     - geo_country
     - geo_region
     - geo_locality
     - geo_iso3166
   * - ``Russia: Novosibirsk, Akademgorodok``
     - Russia
     - Novosibirsk
     - Akademgorodok
     - RU
   * - ``USA: California, San Diego``
     - USA
     - California
     - San Diego
     - US
   * - ``United Kingdom``
     - United Kingdom
     - NaN
     - NaN
     - GB
   * - ``England: Yorkshire``
     - United Kingdom
     - Yorkshire
     - NaN
     - GB
   * - ``Germany: Bavaria``
     - Germany
     - Bavaria
     - NaN
     - DE
   * - ``Pacific Ocean``
     - NaN
     - NaN
     - NaN
     - NaN
   * - ``Pacific Ocean (NE)``
     - NaN
     - NaN
     - NaN
     - NaN
   * - ``USSR``
     - USSR
     - NaN
     - NaN
     - HISTORICAL
   * - ``Turkey: Istanbul``
     - Turkey
     - Istanbul
     - NaN
     - TR
   * - ``45.3 N, 30.1 E``
     - NaN
     - NaN
     - NaN
     - NaN
   * - ``China, Shanghai``
     - China
     - NaN
     - Shanghai
     - CN

One Health Classifier
---------------------

Module: :mod:`biometaharmonizer.one_health`
Class: :class:`~biometaharmonizer.one_health.OneHealthClassifier`

The One Health classifier assigns each record to a standardized category
using deterministic, multi-layer semantic analysis. All biological knowledge
is loaded from ``one_health_dictionaries.json``; no terms are hardcoded in the
Python source.

Constructor Parameters
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   OneHealthClassifier(dictionary_path=None, fuzzy_threshold=92)

- **``dictionary_path``** (default ``None``) — path to a custom
  ``one_health_dictionaries.json`` file. When ``None``, the bundled file at
  ``src/biometaharmonizer/schemas/one_health_dictionaries.json`` is used.
  Raises ``FileNotFoundError`` if a non-``None`` path does not exist, and
  ``ValueError`` if the file is missing required top-level keys.

- **``fuzzy_threshold``** (default ``92``) — minimum ``rapidfuzz.fuzz.WRatio``
  score (0–100) for a fuzzy match to be accepted. Increase for stricter
  matching (fewer false positives); decrease to accept more approximate
  matches. Only applies when ``rapidfuzz`` is installed.

``rapidfuzz`` Optional Dependency
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Fuzzy matching is powered by the ``rapidfuzz`` library (``>=3.0.0``). This
dependency is **optional at runtime**: if ``rapidfuzz`` is not importable,
the classifier logs a WARNING and disables the fuzzy fallback layer
gracefully — all other classification layers (tier1 patterns, host
dictionary, synonym map, setting inference) remain active:

.. code-block:: text

   WARNING  biometaharmonizer.one_health:
     rapidfuzz not installed; fuzzy fallback disabled.
     pip install rapidfuzz>=3.0.0

Records that would have been classified by fuzzy matching alone receive
``one_health_category = "Unclassified"`` when ``rapidfuzz`` is absent.

Text Preprocessing Pipeline
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Before any category lookup the classifier runs the following preprocessing
steps on every value, in order:

1. **Null check** — values matching ``NULL_PATTERNS`` return ``Unclassified``
   immediately.
2. **Underscore normalization** — underscores are replaced with spaces
   (e.g. ``"Environmental_bathroom"`` → ``"Environmental bathroom"``).
3. **Institution/culture-collection stripping** — values that contain
   institution keywords (``university``, ``laboratory``, ``hospital``, etc.)
   or match patterns from ``institution_patterns`` / ``culture_collection_prefixes``
   in the dictionary have those tokens removed. If nothing remains after
   stripping, ``Unclassified`` is returned.
4. **"Animal origin" pattern** — ``"a <specimen> of <animal> origin"``
   patterns are resolved directly via ``host_to_category`` before
   abbreviation expansion.
5. **Abbreviation expansion** (``abbreviation_map``) — tokens matching
   entries in the ``abbreviation_map`` dictionary section are expanded to
   their canonical forms (e.g. ``"CSF"`` → ``"cerebrospinal fluid"``,
   ``"GIT"`` → ``"gastrointestinal tract"``).
6. **Synonym normalization** (``synonym_map``) — multi-word phrases matching
   entries in the ``synonym_map`` dictionary section are replaced with a
   canonical form (e.g. ``"clinical isolate"`` → ``"clinical"``,
   ``"gut flora"`` → ``"intestinal microbiota"``) using longest-match-first
   ordering.
7. **Processing term extraction** — terms in ``processing_terms`` (e.g.
   ``"frozen"``, ``"lyophilized"``) are detected and stored in
   ``one_health_processing``; the matched token is removed from the working
   string so it does not interfere with category matching.
8. **Setting term extraction** — terms in ``setting_patterns`` (e.g.
   ``"hospital"``, ``"clinical"``) are detected and stored in
   ``one_health_setting``; the matched token is removed from the working
   string.

Classification Layers
~~~~~~~~~~~~~~~~~~~~~

After preprocessing, the working string passes through classification in
this priority order:

1. **Tier1 pattern matching** — compiled regex patterns from
   ``tier1_patterns`` in the dictionary, applied in the order defined by
   ``tier1_order``. Returns the first matching category.
2. **Fuzzy matching** (``rapidfuzz``) — if no tier1 pattern matched and
   ``rapidfuzz`` is available, ``WRatio`` similarity is computed against the
   full ``ontology_map`` corpus (filtered to remove ambiguous terms). The
   best match above ``fuzzy_threshold`` is returned.
3. **Unclassified** — if neither layer produces a result.

**Valid output categories for** ``one_health_category``:

- ``Human`` — isolates from human clinical specimens or hosts
- ``Animal`` — domestic and companion animals, livestock, veterinary samples
- ``Plant`` — plant material, rhizosphere, phytopathological samples
- ``Food`` — food products, ingredients, food-processing environments
- ``Environmental`` — soil, sediment, air, water, biofilms not otherwise classified
- ``Unclassified`` — no category could be determined with sufficient confidence

The ``one_health_category`` column is always a string; it is never ``NaN``.
Unclassifiable records receive the string ``"Unclassified"``.

Multi-field Evidence Integration (``classify_multi_field``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:meth:`~biometaharmonizer.one_health.OneHealthClassifier.classify_multi_field`
runs a two-pass evidence integration over up to six input fields:
``isolation_source``, ``host``, ``env_medium``, ``env_local_scale``,
``env_broad_scale``, ``sample_type``.

Field weights (used in the confidence formula):

+----------------------+--------+
| Field                | Weight |
+======================+========+
| ``isolation_source`` | 1.00   |
+----------------------+--------+
| ``host``             | 1.00   |
+----------------------+--------+
| ``env_medium``       | 0.85   |
+----------------------+--------+
| ``env_local_scale``  | 0.80   |
+----------------------+--------+
| ``sample_type``      | 0.70   |
+----------------------+--------+
| ``env_broad_scale``  | 0.50   |
+----------------------+--------+

A **corroboration bonus** of 0.10 is added when two or more fields agree on
the same category.

**Setting-inference fallback:** If no field produces any positive category
signal but a setting term was detected during preprocessing (e.g. the value
contained ``"hospital"`` or ``"clinical"``), the setting is mapped to a
category via ``setting_to_category`` and ``setting_confidence`` from the
dictionary JSON. In this case ``one_health_source_field`` is set to the
special value ``"setting_inference"`` to indicate that the category came
from setting context r