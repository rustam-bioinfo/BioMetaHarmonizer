.. _cli_reference:

=============
CLI Reference
=============

BioMetaHarmonizer installs a ``biometaharmonizer`` entry point that is
registered in ``pyproject.toml`` as::

    biometaharmonizer = "biometaharmonizer.cli:main"

The CLI is built with ``argparse`` and exposes a single subcommand: ``run``.

.. code-block:: bash

   biometaharmonizer --version
   biometaharmonizer run --help

run subcommand
--------------

Runs the full harmonization pipeline: ingest → key-map → date/geo/One Health
→ output.

**Usage:**

.. code-block:: bash

   biometaharmonizer run \
       --input   <FILE_OR_ACCESSIONS> \
       --email   <EMAIL> \
       --output  <FILE> \
       [--api-key <KEY>] \
       [--cache-dir <DIR>] \
       [--format <FORMAT> [<FORMAT> ...]] \
       [--summary <FILE>] \
       [--fetch-batch-size <N>] \
       [--esearch-batch-size <N>] \
       [--refresh-cache] \
       [--verbose]

**Input flexibility:**

The ``--input`` argument accepts:

- A path to a plain-text file containing one accession per line.
- A comma-separated list of accessions passed directly as a string
  (e.g. ``-i "SAMN02436525,SAMN02434874"``). This is detected automatically
  when the argument does not look like an existing file path.

Accepted accession prefixes: ``SAMN``, ``SAME``, ``SAMD`` (BioSample) or
``GCF_``, ``GCA_`` (assembly). Mixed files are handled automatically.

**Output format inference:**

If ``--format`` is not specified, the output format is inferred from the
file extension of ``--output``:

.. list-table:: Format inference from file extension
   :header-rows: 1

   * - Extension
     - Inferred format
   * - ``.csv``
     - ``csv``
   * - ``.tsv``
     - ``tsv``
   * - ``.txt``
     - ``tsv``
   * - ``.xlsx``
     - ``excel``
   * - ``.xls``
     - ``excel``
   * - ``.parquet``
     - ``parquet``
   * - ``.jsonl``
     - ``jsonl``
   * - (other)
     - ``csv``

**Multiple output formats:**

``--format`` accepts one or more space-separated format names. When a single
format is given the ``--output`` path is used as-is. When multiple formats
are given the stem of ``--output`` is reused and the correct extension is
appended for each format (e.g. ``--output out.csv --format csv tsv excel``
produces ``out.csv``, ``out.tsv``, ``out.xlsx``).

Flags
-----

.. list-table:: CLI flags
   :header-rows: 1

   * - Long flag
     - Short
     - Type
     - Default
     - Description
   * - ``--input``
     - ``-i``
     - str
     - —
     - **Required.** Input file or comma-separated accession list.
   * - ``--email``
     - ``-e``
     - str
     - —
     - **Required.** NCBI contact email.
   * - ``--output``
     - ``-o``
     - str
     - —
     - **Required.** Output file path.
   * - ``--api-key``
     - —
     - str
     - None
     - NCBI API key.
   * - ``--cache-dir``
     - —
     - str
     - None
     - Assembly summary cache directory.
   * - ``--format``
     - ``-f``
     - choice(s)
     - None
     - Output format(s): ``csv``, ``tsv``, ``excel``, ``parquet``, ``jsonl``. One or more space-separated values.
   * - ``--summary``
     - —
     - str
     - None
     - Write fill-rate summary CSV to this path.
   * - ``--fetch-batch-size``
     - —
     - int
     - 200
     - Records per efetch request (clamped to 500 maximum).
   * - ``--esearch-batch-size``
     - —
     - int
     - 200
     - Accessions per esearch term.
   * - ``--refresh-cache``
     - —
     - flag
     - False
     - Force re-download of assembly index.
   * - ``--verbose``
     - ``-v``
     - flag
     - False
     - Enable DEBUG-level logging.
   * - ``--version``
     - —
     - flag
     - —
     - Print version string and exit.

Exit Codes
----------

``biometaharmonizer run`` returns one of three exit codes:

.. list-table:: CLI exit codes
   :header-rows: 1

   * - Code
     - Meaning
   * - ``0``
     - Success — output file(s) written.
   * - ``1``
     - User input error — invalid email, input file not found, unrecognized
       format string, or no valid BioSample IDs could be resolved.
   * - ``2``
     - Runtime error — ingestion failure (network, XML parse), empty
       DataFrame after ingestion, write failure, or import error.

CLI Flag ↔ Python API Mapping
-------------------------------

.. list-table:: CLI flags
   :header-rows: 1

   * - CLI flag
     - ``ingest()`` parameter
     - Default
   * - ``--input``
     - ``source``
     - —
   * - ``--email``
     - ``email``
     - —
   * - ``--api-key``
     - ``api_key``
     - ``None``
   * - ``--cache-dir``
     - ``cache_dir``
     - ``None``
   * - ``--fetch-batch-size``
     - ``fetch_batch_size``
     - 200
   * - ``--esearch-batch-size``
     - ``esearch_batch_size``
     - 200
   * - ``--refresh-cache``
     - ``refresh_cache``
     - ``False``

.. note::

   The CLI default for ``--esearch-batch-size`` is 200 (as declared in
   ``add_argument``), while the Python API module-level constant
   ``_ESEARCH_BATCH`` defaults to 100. The effective value is whichever
   is passed to ``ingest()``.

Complete Invocation Examples
-----------------------------

**Example 1 — BioSample file, CSV output with summary:**

.. code-block:: bash

   biometaharmonizer run \
       --input    biosample_ids.txt \
       --email    your@email.com \
       --api-key  abc123def456 \
       --output   harmonized.csv \
       --summary  fill_rates.csv \
       --verbose

**Example 2 — Assembly accessions, Parquet output, custom cache:**

.. code-block:: bash

   biometaharmonizer run \
       --input           assemblies.txt \
       --email           your@email.com \
       --output          harmonized.parquet \
       --cache-dir       /data/bmh_cache \
       --fetch-batch-size 500 \
       --refresh-cache

**Example 3 — Inline accessions (no file required):**

.. code-block:: bash

   biometaharmonizer run \
       -i "SAMN02436525,SAMN02434874,SAMN02429261" \
       -e your@email.com \
       -o out.csv

**Example 4 — Multiple output formats at once:**

.. code-block:: bash

   biometaharmonizer run \
       --input  ids.txt \
       --email  your@email.com \
       --output harmonized.csv \
       --format csv tsv excel jsonl

Log Output Format
-----------------

Log messages are written to ``stderr`` using the format:

.. code-block:: text

   HH:MM:SS  LEVEL    logger_name: message

For example:

.. code-block:: text

   14:32:01  INFO     biometaharmonizer.ingestion: Fetching NCBI assembly index (refseq) ...
   14:32:45  INFO     biometaharmonizer.ingestion: Fetching metadata for 1500 BioSample accessions...
   14:35:12  INFO     biometaharmonizer.ingestion: ============================================================
   14:35:12  INFO     biometaharmonizer.ingestion: INGEST SUMMARY
   14:35:12  INFO     biometaharmonizer.ingestion:   Input IDs provided  : 1500
   14:35:12  INFO     biometaharmonizer.ingestion:   fetch_batch_size    : 200
   14:35:12  INFO     biometaharmonizer.ingestion:   esearch_batch_size  : 200
   14:35:12  INFO     biometaharmonizer.ingestion:   Records in output   : 1498
   14:35:12  INFO     biometaharmonizer.ingestion:   bioproject_accession filled : 1350 / 1498
   14:35:12  INFO     biometaharmonizer.ingestion:   assembly_accession_refseq   filled : 1200 / 1498
   14:35:12  INFO     biometaharmonizer.ingestion:   assembly_accession_genbank  filled : 1100 / 1498
   14:35:12  INFO     biometaharmonizer.ingestion: ============================================================
   14:35:14  INFO     biometaharmonizer.cli: Writing output to harmonized.csv (format=csv)
   Done. 1498 records x 57 columns -> harmonized.csv

The final ``Done.`` line is printed to ``stdout``.
