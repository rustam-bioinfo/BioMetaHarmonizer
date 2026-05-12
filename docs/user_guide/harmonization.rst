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

.. list-table:: Geographic string parsing examples
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
- **Parenthetical qualifiers:** trailing parenthetical suffixes such as
  ``"United Kingdom (England, Wales & N. Ireland)"`` are stripped before
  country lookup so the comma inside the parentheses does not break parsing.
- **Ocean/sea lookup:** when the country token (after stripping parenthetical
  qualifiers) matches one of the named ocean/sea entries, the value is
  stored in ``geo_sea_ocean`` instead of ``geo_country``.

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

**Valid output categories for** ``one_health_category``:

- ``Human`` — isolates from human clinical specimens or hosts
- ``Animal`` — domestic and companion animals, livestock, veterinary samples
- ``Plant`` — plant material, rhizosphere, phytopathological samples
- ``Food`` — food products, ingredients, food-processing environments
- ``Environmental`` — soil, sediment, air, water, biofilms not otherwise classified
- ``Unclassified`` — no category could be determined with sufficient confidence

The ``one_health_category`` column is always a string; it is never ``NaN``.
Unclassifiable records receive the string ``"Unclassified"``.

**Public methods:**

- :meth:`~biometaharmonizer.one_health.OneHealthClassifier.classify` — single-field
  classification from ``isolation_source``; returns a :class:`pandas.Series`.
- :meth:`~biometaharmonizer.one_health.OneHealthClassifier.classify_joint` — two-field
  classification from ``isolation_source`` and ``host``; delegates to
  ``classify_multi_field`` and returns the ``one_health_category`` Series.
- :meth:`~biometaharmonizer.one_health.OneHealthClassifier.classify_with_confidence` —
  single-field with confidence; returns a DataFrame with columns
  ``one_health_category``, ``one_health_term``, ``one_health_confidence``.
- :meth:`~biometaharmonizer.one_health.OneHealthClassifier.classify_multi_field` —
  full multi-field evidence integration accepting named Series for any of:
  ``isolation_source``, ``host``, ``env_medium``, ``env_local_scale``,
  ``env_broad_scale``, ``sample_type``.

**Confidence model:**

.. code-block:: text

   confidence = min(1.0, term_specificity * field_weight + corroboration_bonus)

Term specificity values:

- Unambiguous list or host dict hit: **1.0**
- Tier1 phrase ≥ 8 characters: **0.90**
- Tier1 term 4–7 characters: **0.75**
- Tier1 term < 4 characters: **0.50**
- Ambiguous specimen term: **0.3**

Confidence is discretized by ``discretize_confidence()``::

   >= 0.85  → "high"
   >= 0.60  → "medium"
   >= 0.30  → "low"
   <  0.30  → "unresolved"

Category and Example Isolation Sources
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table:: Geographic string parsing examples
   :header-rows: 1

   * - Category
     - Example ``isolation_source`` or ``host`` values
   * - ``Human``
     - blood, urine, cerebrospinal fluid, rectal swab, wound, surgical site, sputum, Homo sapiens
   * - ``Animal``
     - bovine feces, swine nasal swab, chicken, cow, dog, pig, horse fecal, poultry litter, Bos taurus
   * - ``Plant``
     - plant root, rhizosphere, leaf surface, tomato, rice, wheat stem, Arabidopsis
   * - ``Food``
     - ground beef, raw milk, cheese, lettuce, retail chicken, food processing surface, ready-to-eat meat
   * - ``Environmental``
     - soil, river sediment, wastewater, biofilm, air sample, drinking water, estuary water
   * - ``Unclassified``
     - terms with no resolvable category evidence

Synonym Resolution
------------------

Module: :mod:`biometaharmonizer.synonyms`
Function: :func:`~biometaharmonizer.synonyms.build_synonym_lookup`

The synonym lookup table maps every lowercased synonym to a canonical standard
key. It is built in two layers and cached via ``functools.lru_cache`` for the
lifetime of the process:

**Layer 1 — unified.json:** Project-defined synonyms. Each field entry has a
``standard_key`` and a list of ``synonyms``. All synonyms are lowercased and
mapped to the ``standard_key``.

**Layer 2 — ncbi_attributes.xml (optional):** NCBI BioSample official
``HarmonizedName`` and ``Synonym`` entries. Present only after running
``scripts/build_ncbi_attribute_cache.py``. Layer 2 overwrites Layer 1
conflicts with the authoritative NCBI mapping.

Selected synonym → canonical key mappings (from ``unified.json``):

.. list-table:: Geographic string parsing examples
   :header-rows: 1

   * - Synonym (lowercased)
     - Canonical key
   * - ``collection date``
     - ``collection_date``
   * - ``collection_date``
     - ``collection_date``
   * - ``geo_loc_name``
     - ``geo_loc_name``
   * - ``geographic location``
     - ``geo_loc_name``
   * - ``geographic_location``
     - ``geo_loc_name``
   * - ``host organism``
     - ``host``
   * - ``isolation source``
     - ``isolation_source``
   * - ``isolation_source``
     - ``isolation_source``
   * - ``collected by``
     - ``collected_by``
   * - ``sample type``
     - ``sample_type``

Null Normalization in Harmonization Engines
--------------------------------------------

Both :class:`~biometaharmonizer.date_engine.DateEngine` and
:class:`~biometaharmonizer.geo_engine.GeoEngine` maintain their own
``NULL_PATTERNS`` class attribute (a compiled regex). The date engine's
pattern covers: ``missing``, ``unknown``, ``n/a``, ``not provided``,
``not collected``, ``na``, ``none``, ``--``, and any ``missing:.*``,
``not applicable:.*``, or ``restricted access`` variant. The geo engine
uses the same comprehensive pattern as the ingestion module.