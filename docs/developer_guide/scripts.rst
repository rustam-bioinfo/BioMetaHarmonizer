.. _scripts:

======================
Developer Scripts
======================

The ``scripts/`` directory contains three standalone maintenance scripts for
contributors and power users who build or refresh the data assets consumed at
runtime. None of these scripts import from the ``biometaharmonizer`` package
itself; they are intentionally standalone.

build_dictionaries.py
----------------------

**Purpose**

``scripts/build_dictionaries.py`` builds the enriched
``one_health_dictionaries.json`` file that powers the
:class:`~biometaharmonizer.one_health.OneHealthClassifier`. It queries three
external sources:

1. **OLS4 API** — Environmental Ontology (ENVO), FoodOn, UBERON, and Plant
   Ontology for ``Environmental``, ``Food``, ``_anatomy``, and ``Plant``
   category terms.
2. **NCBI Taxonomy local dump** — builds ``host_to_category`` mappings for
   vertebrate animals and plants by walking the taxonomic tree from
   configured root taxon IDs.
3. **UMLS API** (optional, requires API key) — synonym expansion for 17
   clinical specimen CUIs.

The hand-curated base file is loaded first. The merge strategy is
**base_wins**: any key already present in the base dictionary is never
overwritten by ontology-derived data. This preserves expert-curated entries
against automated ontology updates.

OLS4 Integration
~~~~~~~~~~~~~~~~~

The following ontologies are queried, mapped via ``OLS_ONTOLOGY_MAP``:

.. list-table:: build_dictionaries.py CLI flags
   :header-rows: 1

   * - Ontology
     - OLS ID
     - One Health category
     - Seed IRIs (short form)
   * - ENVO
     - ``envo``
     - Environmental
     - ENVO:00000428, ENVO:00010483, ENVO:01000254, ENVO:01001110, ENVO:00000063, ENVO:00000015, ENVO:00000873, ENVO:00000134, ENVO:00002006
   * - FoodOn
     - ``foodon``
     - Food
     - FOODON:00001002, FOODON:03400361, FOODON:00001709, FOODON:03420194
   * - UBERON
     - ``uberon``
     - _anatomy (split post-fetch)
     - UBERON:0000465
   * - PO
     - ``po``
     - Plant
     - PO:0025131

The OLS4 ``hierarchicalDescendants`` endpoint is used to traverse the class
hierarchy. Only ``hasExactSynonym`` annotations are collected (not broad,
narrow, or related) to minimise false-positive category assignments.

Each raw OLS term string is cleaned by ``_clean_ols_term()`` which applies
six rules in order:

1. Strip OLS language/scope tags: ``(exact)``, ``(related)``, etc.
2. Strip parenthetical scope tags that appear after a comma.
3. Reject GS1 GPC catalogue codes (e.g. ``"0900000 - cereals (GS1 GPC)"``).
4. Reject regulatory catalogue codes matching ``_RE_REGULATORY_CATALOGUE``,
   which covers EFSA FoodEx2 codes, EC codes, EuroFIR, EFG, CIAA, CCFAC,
   and Codex entries. These are classification artefacts, not free-text terms
   that could match BioSample metadata.
5. Reject terms of fewer than 2 characters.
6. Return ``None`` to signal that the term should be discarded.

UBERON Anatomy Classification
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

UBERON terms under ``UBERON:0000465`` (material anatomical entity) are split
into three buckets using membership in two constant sets:

- **``_uberon_human``** — terms in ``UBERON_HUMAN_EXCLUSIVE``: cerebrospinal
  fluid, pleural fluid, peritoneal fluid, synovial fluid, amniotic fluid,
  dialysate, bronchoalveolar lavage, sputum, dental plaque, catheter,
  central venous.
- **``_uberon_animal``** — terms in ``UBERON_ANIMAL_EXCLUSIVE``: rumen,
  reticulum, omasum, abomasum, gizzard, proventriculus, crop, cloaca, swim
  bladder, gill, hemolymph, exoskeleton.
- **``_uberon_ambiguous``** — all remaining UBERON anatomy terms that cannot
  be assigned to either set; stored in ``ambiguous_specimen_terms``.

NCBI Taxonomy Integration
~~~~~~~~~~~~~~~~~~~~~~~~~~

A BFS walk of the NCBI taxonomy tree is performed from the following root
taxon IDs (``NCBI_TAXON_ROOTS``):

.. list-table:: build_dictionaries.py CLI flags
   :header-rows: 1

   * - Taxon ID
     - Name
     - Category
   * - 9606
     - Homo sapiens
     - Human
   * - 40674
     - Mammalia
     - Animal
   * - 8782
     - Aves
     - Animal
   * - 8504
     - Reptilia
     - Animal
   * - 8292
     - Amphibia
     - Animal
   * - 7776
     - Chondrichthyes
     - Animal
   * - 7898
     - Actinopterygii
     - Animal
   * - 6656
     - Arthropoda
     - Animal
   * - 6447
     - Mollusca
     - Animal
   * - 6231
     - Nematoda
     - Animal
   * - 6340
     - Annelida
     - Animal
   * - 7586
     - Echinodermata
     - Animal
   * - 6073
     - Cnidaria
     - Animal
   * - 6040
     - Porifera
     - Animal
   * - 33090
     - Viridiplantae
     - Plant
   * - 2763
     - Rhodophyta
     - Plant
   * - 3041
     - Chlorophyta
     - Plant
   * - 2870
     - Phaeophyceae
     - Plant

.. note::

   **Homo sapiens (txid 9606)** is treated as an exact match only — its
   subtree is not walked, because the subtree contains only subspecies/race
   taxa that should not produce additional entries. **Fungi (txid 4751)** are
   intentionally excluded: their One Health category is context-dependent
   (Environmental pathogen, Food spoilage, Animal/Human mycosis) and cannot
   be determined from taxonomy alone.

Name strings are extracted from ``names.dmp`` using only the following
``name_class`` values (``NAMES_DMP_KEEP_CLASSES``):

- ``scientific name``
- ``common name``
- ``genbank common name``
- ``equivalent name``

Names of 1–3 tokens are kept; longer names are excluded to reduce noise.

The ``--taxdmp`` argument accepts three input forms:

- A path to a pre-downloaded ``taxdmp.zip`` file.
- A path to an extracted directory containing ``names.dmp`` and ``nodes.dmp``.
- Omitted entirely, in which case ``taxdmp.zip`` is downloaded automatically
  from ``https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdmp.zip``
  (approximately **65 MB**).

Collision Resolution
~~~~~~~~~~~~~~~~~~~~~

``_resolve_collisions(base)`` detects terms that appear in multiple categories
or conflict between dictionary sections. Two collision types are handled:

1. **Intra-ontology_map:** a term string appears in two or more category lists
   within ``ontology_map`` (e.g. ``"blood"`` in both ``Food`` and ``Animal``).
2. **Cross-section:** a term in ``ontology_map`` also exists in
   ``host_to_category``, ``unambiguous_human_terms``,
   ``unambiguous_animal_terms``, or ``ambiguous_specimen_terms``.

In both cases the term is removed from its ``ontology_map`` category list(s)
and appended to ``base["ambiguous_category_terms"]`` with a list of
conflicting source labels. The ``base_wins`` exemption applies: any term that
was present in the hand-curated base ``ontology_map`` before this build run
is excluded from collision processing.

UMLS Integration
~~~~~~~~~~~~~~~~~

When ``--umls-key`` is provided, synonym expansion is performed for
17 clinical specimen CUIs via the UMLS TGT → service-ticket authentication
flow:

1. A TGT (Ticket Granting Ticket) is obtained by POST to the UMLS UTS API
   with the API key.
2. For each CUI in ``UMLS_SPECIMEN_CUIS``, a service ticket is requested and
   used to query the CUI's atom list for English synonyms.

The 17 CUIs and their canonical names:

.. list-table:: build_dictionaries.py CLI flags
   :header-rows: 1

   * - CUI
     - Canonical name
   * - C0005767
     - blood
   * - C0042036
     - urine
   * - C0038569
     - sputum
   * - C0007555
     - cerebrospinal fluid
   * - C0205189
     - pleural fluid
   * - C0003967
     - ascitic fluid
   * - C0039981
     - synovial fluid
   * - C0006252
     - bronchial lavage
   * - C0444941
     - wound
   * - C0000735
     - abscess
   * - C0032227
     - pus
   * - C0015411
     - feces
   * - C0521481
     - rectal swab
   * - C0029001
     - oral swab
   * - C0042048
     - vaginal swab
   * - C0877612
     - nasal swab
   * - C0586478
     - throat swab

CLI Flags
~~~~~~~~~~

All flags are derived from ``parse_args()`` in the script:

.. list-table:: ``build_dictionaries.py`` CLI flags
   :header-rows: 1
   :widths: 20 80

   * - Flag
     - Description
   * - ``--base``
     - Path to the hand-curated base JSON.
       Default: ``src/biometaharmonizer/schemas/one_health_dictionaries.json``
   * - ``--output``
     - Output path for the enriched JSON.
       Default: same as ``--base`` (overwrites in place).
   * - ``--taxdmp``
     - Path to ``taxdmp.zip`` or an extracted directory containing
       ``names.dmp`` and ``nodes.dmp``. Omit to trigger automatic
       download (~65 MB) from NCBI FTP.
   * - ``--umls-key``
     - UMLS API key for synonym expansion. Omit to skip UMLS.
   * - ``--skip-ols``
     - Skip all OLS4 queries.
   * - ``--skip-ncbi``
     - Skip NCBI Taxonomy processing.
   * - ``--dry-run``
     - Build the enriched dict in memory but do not write to disk.

Usage Examples
~~~~~~~~~~~~~~

.. code-block:: bash

   # Full run — overwrites bundled dictionary in place:
   python scripts/build_dictionaries.py \
       --base    src/biometaharmonizer/schemas/one_health_dictionaries.json \
       --output  src/biometaharmonizer/schemas/one_health_dictionaries.json

   # Use a pre-downloaded taxdmp.zip to skip the ~65 MB download:
   python scripts/build_dictionaries.py --taxdmp /path/to/taxdmp.zip

   # Skip NCBI taxonomy entirely:
   python scripts/build_dictionaries.py --skip-ncbi

   # Full run with UMLS synonym expansion:
   python scripts/build_dictionaries.py --umls-key YOUR_UMLS_API_KEY

When to Re-run
~~~~~~~~~~~~~~

Re-run this script when:

- NCBI taxonomy is updated and new host names need to be incorporated into
  ``host_to_category``.
- New OLS ontology versions are released with additional terms.
- New One Health categories are required (add them to the base JSON first,
  then re-run to merge ontology data).
- After adding new hand-curated entries to the base JSON to propagate
  collision resolution correctly.

build_ncbi_attribute_cache.py
-------------------------------

**Purpose**

``scripts/build_ncbi_attribute_cache.py`` fetches the official NCBI BioSample
attribute harmonization XML from NCBI and saves it as
``src/biometaharmonizer/schemas/ncbi_attributes.xml``.

This file is **Layer 2** of the synonym lookup used by
:func:`~biometaharmonizer.synonyms.build_synonym_lookup`. Without it, only
Layer 1 (``unified.json``) is active and some NCBI ``HarmonizedName``
attributes may not be recognized. The file is parsed at runtime by
:func:`~biometaharmonizer.synonyms.build_synonym_lookup` every time the
process starts (cached via ``lru_cache`` thereafter).

**Output file:**

``src/biometaharmonizer/schemas/ncbi_attributes.xml``

This is the raw XML response from:
``https://www.ncbi.nlm.nih.gov/biosample/docs/attributes/?format=xml``

**CLI Flags:**

.. list-table:: build_dictionaries.py CLI flags
   :header-rows: 1

   * - Flag
     - Description
   * - ``--output-dir``
     - Directory to write ``ncbi_attributes.xml`` into. Default: ``src/biometaharmonizer/schemas/``
   * - ``--skip-fetch``
     - Skip the network request. Validate and report an existing ``ncbi_attributes.xml`` only.

The script retries up to 3 times with exponential backoff (2 s, 4 s) on
network failures (``MAX_ATTEMPTS = 3``, ``TIMEOUT = 30`` seconds).

After saving, ``parse_and_report()`` prints the count of ``HarmonizedName``
entries and ``Synonym`` entries found in the XML.

**Usage examples:**

.. code-block:: bash

   # Fetch and save to default location:
   python scripts/build_ncbi_attribute_cache.py

   # Save to a custom directory:
   python scripts/build_ncbi_attribute_cache.py --output-dir /tmp/schemas

   # Validate an existing file without a network request:
   python scripts/build_ncbi_attribute_cache.py --skip-fetch

**When to re-run:**

Re-run periodically (e.g. monthly) or whenever NCBI adds or renames BioSample
attributes. The tool functions without this file (Layer 2 disabled) but
synonym coverage will be lower for packages that use non-standard attribute
names that are only defined in the NCBI XML.

generate_summary_report.py
---------------------------

**Purpose**

``scripts/generate_summary_report.py`` generates a comprehensive visual
summary report from a BioMetaHarmonizer output file. It reads a CSV/TSV/
Parquet file produced by :func:`~biometaharmonizer.output.write` and produces
an interactive HTML report (and optionally JSON and CSV summaries) with
Plotly visualizations.

**Input:**

A harmonized DataFrame file produced by ``biometaharmonizer`` (CSV, TSV, or
Parquet). The script loads it with ``pandas``.

**CLI Flags:**

.. list-table:: build_dictionaries.py CLI flags
   :header-rows: 1

   * - Flag
     - Description
   * - ``--input, -i``
     - **Required.** Path to the input harmonized data file.
   * - ``--output, -o``
     - Output file path (for single-format output).
   * - ``--output-dir, -d``
     - | Output directory for multi-format output.
   * - ``--formats, -f``
     - One or more of: ``html``, ``json``, ``csv``. Default: inferred from ``--output`` suffix.
   * - ``--verbose, -v``
     - Enable DEBUG-level logging.

**Output sections and visualizations generated:**

The HTML report is produced by ``generate_full_html_report()`` and includes:

1. **Data Quality Dashboard** (``generate_quality_dashboard()``) — four-panel
   subplot: fill rates by category (bar chart), overall completeness
   distribution (histogram), category-wise average fill rate (bar), and
   top-15 most complete columns (bar).

2. **Geospatial Visualizations** (``generate_geo_visualizations()``) — country
   distribution choropleth or bar chart based on ``geo_country`` and
   ``geo_iso3166`` columns.

3. **Temporal Analysis** (``generate_temporal_analysis()``) — time-series
   distribution of ``collection_date`` values grouped by year or year-month.

4. **One Health Chart** (``generate_one_health_chart()``) — pie or bar chart
   of ``one_health_category`` distribution.

5. **Host Analysis** (``generate_host_analysis()``) — top host values from
   the ``host`` column.

6. **Extra Attributes Analysis** (``generate_extra_attributes_analysis()``) —
   summary of keys present in ``_extra_attributes`` across all records,
   including antibiogram presence rate.

**Metrics computed** (``compute_fill_rates()`` and ``generate_json_metrics()``):

- Per-column ``non_null_count``, ``null_count``, ``fill_pct`` for all 57 schema
  columns.
- Category-level average fill rates (using ``COLUMN_CATEGORIES`` groupings).
- Overall dataset completeness summary.
- One Health category distribution counts and percentages.
- Temporal coverage statistics (min/max year, year distribution).
- Geographic coverage (unique countries, coverage by ISO3166).
- Top host values and their frequencies.
- ``_extra_attributes`` key frequency table.

**Usage examples:**

.. code-block:: bash

   # Generate HTML report only:
   python scripts/generate_summary_report.py \
       --input harmonized.csv \
       --output report.html

   # Generate all formats:
   python scripts/generate_summary_report.py \
       --input harmonized.csv \
       --output-dir reports/ \
       --formats html json csv

   # Verbose logging:
   python scripts/generate_summary_report.py \
       -i harmonized.parquet -o report.html -v

.. note::

   Plotly must be installed for HTML/PDF output (``pip install plotly``).
   PDF export additionally requires ``kaleido`` (``pip install kaleido``).
   The script imports Plotly conditionally and will still produce JSON and
   CSV summaries if Plotly is absent.