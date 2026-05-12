.. _ingestion:

=========
Ingestion
=========

This page documents the behavior of :mod:`biometaharmonizer.ingestion` in
full, derived entirely from its source code.

Overview
--------

:func:`~biometaharmonizer.ingestion.ingest` is the single entry point for all
data retrieval. It accepts a list of NCBI accessions (or a path to a text file
containing them), fetches the corresponding BioSample XML records from NCBI
Entrez, parses and harmonizes each record, and returns a
:class:`pandas.DataFrame` conforming to the fixed 57-column output schema
defined in ``_load_final_schema()``.

Module-level Configuration Functions
-------------------------------------

Three module-level setters configure shared state before (or instead of)
passing arguments directly to :func:`~biometaharmonizer.ingestion.ingest`.
All three write module-level globals and are **not thread-safe** — see
:ref:`thread-safety` below.

.. function:: set_email(email: str) -> None

   Set ``ENTREZ_EMAIL`` and ``Bio.Entrez.email``. Validated against the
   pattern ``^[^@\s]+@[^@\s]+\.[^@\s]+$``. In addition to format
   validation, the following well-known placeholder addresses are explicitly
   rejected with ``ValueError`` because NCBI guidelines require a real
   contact address:

   .. code-block:: text

      your@email.com
      example@example.com
      user@example.org
      test@test.com
      email@example.com

   :raises ValueError: For addresses that fail the regex or match any of the
       placeholder strings above.

.. function:: set_api_key(key: str) -> None

   Set ``ENTREZ_API_KEY`` and ``Bio.Entrez.api_key``. Raises the NCBI rate
   limit from 3 to 10 requests/second, roughly tripling throughput on large
   datasets. There is no format validation; any string is accepted.

   .. code-block:: python

      import biometaharmonizer as bmh
      bmh.set_api_key("abc123def456...")
      df = bmh.ingest(source="ids.txt", email="researcher@institution.edu")

.. function:: set_cache_dir(path) -> None

   Override ``CACHE_DIR`` and call
   ``_read_assembly_summary_cached.cache_clear()`` to invalidate the
   internal ``functools.lru_cache`` on the assembly summary reader.
   **Side effect:** any assembly summary data cached in memory from a
   previous call to ``ingest()`` in the same process is discarded.
   Subsequent calls will re-read from the new path (or re-download if the
   files are absent).

   .. code-block:: python

      import biometaharmonizer as bmh
      bmh.set_cache_dir("/content/bmh_cache")   # Google Colab example

ID Classification
-----------------

Before any network request, ``_classify_ids()`` partitions the input into three
buckets by prefix:

- **Assembly accessions** (``GCF_``, ``GCA_``) — require two-step resolution
  to BioSample accessions.
- **BioSample accessions** (``SAMN``, ``SAME``, ``SAMD``) — fetched directly.
- **Unrecognized IDs** — logged at WARNING level and skipped entirely; they
  produce no rows in the output.

The classification is case-insensitive and checked via ``startswith``.

Input Deduplication
-------------------

After loading IDs from a file or list, ``_deduplicate()`` removes exact
duplicate accession strings before any network call. The number of removed
duplicates is logged at INFO level:

.. code-block:: text

   INFO  biometaharmonizer.ingestion: Removed 12 duplicate input IDs.

Empty Input Behavior
--------------------

If the deduplicated accession list is empty (because the input file was
empty or contained only whitespace/blank lines), ``ingest()`` logs a WARNING
and returns an **empty DataFrame** with all 57 schema columns — it does not
raise an exception:

.. code-block:: text

   WARNING  biometaharmonizer.ingestion:
     ingest() called with an empty accession list. Returning empty DataFrame.

This is distinct from the ``ValueError`` case below.

Error Cases
-----------

``ingest()`` raises the following exceptions:

- **``ValueError``** — raised when no valid email is provided (neither via
  the ``email`` parameter nor via a prior :func:`set_email` call), or when
  all IDs are unrecognized/unresolvable and the final BioSample list is
  empty after classification and assembly resolution. The error message
  includes a human-readable explanation:

  .. code-block:: text

     ValueError: No valid BioSample IDs could be resolved. Reasons:
       12 unrecognized IDs (expected SAMN/SAME/SAMD/GCF/GCA prefixes)

- **``FileNotFoundError``** — raised by ``_load_ids()`` when ``source`` is a
  string or ``Path`` that looks like a file path but does not exist on disk:

  .. code-block:: text

     FileNotFoundError: Input file not found: /data/missing.txt

Assembly Accession Resolution
------------------------------

Assembly accessions cannot be fetched directly from the ``biosample`` Entrez
database. They are resolved to BioSample accessions via two sequential passes:

**Pass 1 — Local assembly index:**

The function ``_ensure_assembly_summaries()`` downloads and caches two NCBI
assembly summary flat files on first use:

- ``assembly_summary_refseq.txt`` from
  ``https://ftp.ncbi.nlm.nih.gov/genomes/ASSEMBLY_REPORTS/assembly_summary_refseq.txt``
- ``assembly_summary_genbank.txt`` from
  ``https://ftp.ncbi.nlm.nih.gov/genomes/ASSEMBLY_REPORTS/assembly_summary_genbank.txt``

Both are stored in ``CACHE_DIR`` (default: ``~/.biometaharmonizer/cache/``).
The TTL is **7 days** (``_CACHE_TTL_DAYS``). Files older than this are
deleted and re-downloaded automatically. Setting ``refresh_cache=True``
forces deletion and re-download regardless of age.

Once cached, ``_resolve_assembly_to_biosample()`` searches both files for
matching ``assembly_accession`` values and extracts the corresponding
``biosample`` column value.

**Pass 2 — Entrez elink fallback:**

Any assembly accession not found in the local index is forwarded to
``_resolve_gcx_via_entrez()``, which performs:

.. code-block:: text

   esearch(db="biosample",
           term="GCF_XXXXXX.1[Accession] OR ...",
           usehistory="n")
   esummary(db="biosample", id=<UIDs from esearch>)

The ``Accession`` field from each ``DocumentSummary`` element is extracted
and mapped back to the original GCF/GCA input.

esearch + efetch Pipeline for BioSample Accessions
----------------------------------------------------

For native BioSample accessions the pipeline is:

.. code-block:: text

   esearch(db="biosample",
           term="SAMN...[Accession] OR ...",
           usehistory="y")         <- per batch
   efetch(db="biosample",
          WebEnv=<env>,
          query_key=<key>,
          retstart=0,
          retmax=fetch_batch_size,
          rettype="full",
          retmode="xml")           <- one call per page

The ``usehistory="y"`` flag instructs NCBI to store the result set in a
named server-side History slot (``WebEnv`` + ``query_key``). Each
:func:`~biometaharmonizer.ingestion.ingest` call creates a **fresh** History
slot per batch; this avoids the cross-batch accumulation bug where a single
``query_key`` would incorrectly index only the final batch.

Batch Size Constants
--------------------

+---------------------+---------+-------------------------------------+
| Constant            | Default | Controls                            |
+=====================+=========+=====================================+
| ``_BATCH_SIZE``     | 200     | Records per ``efetch`` request.     |
|                     |         | Override via ``fetch_batch_size``.  |
+---------------------+---------+-------------------------------------+
| ``_ESEARCH_BATCH``  | 100     | Accessions per ``esearch`` term     |
|                     |         | (BioSample and elink paths).        |
|                     |         | Override via ``esearch_batch_size``.|+---------------------+---------+-------------------------------------+

Higher ``fetch_batch_size`` values reduce HTTP round trips but produce larger
XML payloads per response and may increase per-request latency.

.. note::

   ``fetch_batch_size`` is **silently clamped** to ``_NCBI_EFETCH_MAX = 500``.
   Passing a value above 500 triggers a WARNING log and the effective batch
   size is reduced to 500 to prevent truncated XML responses from NCBI::

      WARNING: fetch_batch_size=1000 exceeds NCBI-recommended maximum of 500.
               Clamping to 500 to avoid truncated XML responses.

Rate Limits and Inter-Batch Sleep
----------------------------------

NCBI enforces the following limits:

- **Without API key:** 3 requests/second (``inter_batch_sleep = 0.34`` s)
- **With API key:** 10 requests/second (``inter_batch_sleep = 0.12`` s)

The ``inter_batch_sleep`` variable is set inside ``_fetch_biosample_metadata``
by checking the module-level ``ENTREZ_API_KEY``. The sleep is applied after
every ``esearch`` batch and after every ``efetch`` call.

Retry Logic
-----------

Every Entrez call is wrapped in a retry loop using the following constants:

+-------------------+-------+---------------------------------------------------+
| Constant          | Value | Meaning                                           |
+===================+=======+===================================================+
| ``_MAX_RETRIES``  | 3     | Maximum number of attempts per call.              |
+-------------------+-------+---------------------------------------------------+
| ``_RETRY_BASE_S`` | 2     | Base of the exponential backoff (seconds).        |
+-------------------+-------+---------------------------------------------------+
| ``_RETRY_MAX_S``  | 30    | Maximum wait between retries (seconds).           |
+-------------------+-------+---------------------------------------------------+

The wait duration for attempt ``n`` (1-indexed) is:

.. code-block:: text

   wait = min(_RETRY_BASE_S ** n, _RETRY_MAX_S)
         = min(2^n, 30)   seconds

So the sequence is: 2 s, 4 s, 8 s (capped at 30 s for large ``n``).

Retries are triggered by instances of ``_TRANSIENT_EXCEPTIONS``:
``urllib.error.URLError``, ``http.client.HTTPException``, ``ConnectionError``,
``TimeoutError``, and ``OSError``.

HTTP 429 Handling
~~~~~~~~~~~~~~~~~

NCBI HTTP 429 ("Too Many Requests") is treated **separately** from the
normal transient exception backoff. When a 429 is detected the retry logic
waits a **flat 60 seconds** (``_HTTP_429_WAIT_S = 60``) before the next
attempt — regardless of the attempt number — and logs at WARNING level:

.. code-block:: text

   WARNING  biometaharmonizer.ingestion:
     HTTP 429 (rate limit) from NCBI. Waiting 60s before retry 2/3...

This flat wait is longer than the normal exponential sequence (2 s, 4 s, 8 s)
because 429 signals that NCBI's rate-limiting window has not yet expired.
As soon as NCBI returns a non-429 response the normal backoff resumes.

.. note::

   The last retry attempt (attempt ``_MAX_RETRIES``) never sleeps, regardless
   of error type — it tries immediately and then gives up if it fails.

Null Normalization
------------------

Every attribute value parsed from NCBI XML is passed through
``_normalize_null(value)``. A value is normalized to ``None`` if it is:

- Literally ``None`` or a pandas NA.
- Empty string or whitespace-only.
- Matched by the ``_NULL_PATTERNS`` regex (case-insensitive):

  - **Dash/dot placeholders:** ``-``, ``--``, ``.``, ``...``
  - **Explicit nulls:** ``n/a``, ``na``, ``nd``, ``nr``, ``ns``, ``nt``,
    ``none``, ``null``, ``nil``
  - **Missing variants:** ``missing``, ``misssing``, ``missng``, ``mising``
  - **Unknown variants:** ``unknown``, ``unkown``, ``unknwon``, ``unknow``
  - **"Not X" phrases:** ``not provided``, ``not collected``,
    ``not applicable``, ``not available``, ``not determined``,
    ``not recorded``, ``not reported``, ``not known``, ``not given``,
    ``not stated``, ``not specified``, ``not done``, ``not tested``,
    ``not sequenced``, ``not typed``
  - **Other descriptors:** ``unavailable``, ``unspecified``,
    ``undetermined``, ``unidentified``
  - **Restricted/access terms:** ``restricted``, ``restricted access``,
    ``withheld``, ``confidential``
  - **Placeholder abbreviations:** ``tbd``, ``tba``
  - **Prefixed null phrases:** any string matching
    ``missing:.*``, ``not applicable:.*``, or
    ``data agreement established pre-2023``

Suppressed and Invalid Accessions
-----------------------------------

When a BioSample accession is suppressed, withdrawn, or otherwise invalid,
NCBI returns no ``<BioSample>`` element for it in the efetch XML response. The
accession simply produces no row in the output. At the end of ``ingest()``,
the number of records returned is logged; discrepancies between input count
and output row count indicate suppressed or invalid accessions:

.. code-block:: text

   WARNING  biometaharmonizer.ingestion:
     NOT resolved (suppressed/invalid after both passes): 2
     Unresolved: ['GCF_000000001.1', 'SAMN00000000']

Ingest Summary Log
------------------

At the end of every successful ``ingest()`` call the following lines are
emitted at INFO level:

.. code-block:: text

   ============================================================
   INGEST SUMMARY
     Input IDs provided  : <N>
     Assembly accessions : <n_gcx>          (only if GCF/GCA input)
       Resolved via local index : <n>
       Resolved via Entrez elink (fallback) : <n>
     fetch_batch_size    : <N>
     esearch_batch_size  : <N>
     Records in output   : <N>
     bioproject_accession filled : <n> / <N>
     assembly_accession_refseq   filled : <n> / <N>
     assembly_accession_genbank  filled : <n> / <N>
   ============================================================

BioProject and Assembly Back-Fill
----------------------------------

After fetching BioSample XML, ``ingest()`` performs a second pass against the
local assembly index to back-fill ``bioproject_accession``,
``assembly_accession_refseq``, and ``assembly_accession_genbank`` columns for
records whose BioSample accession appears in the index. This is logged at INFO
level with the count of records updated.

_extra_attributes
-----------------

Any BioSample attribute whose ``harmonized_name`` (or ``attribute_name``) does
not resolve to a known final output column via the synonym lookup is stored in
the ``_extra_attributes`` column as a JSON-serialized dict.

**Pipe-separated overflow values:** When the same key appears multiple times on
a single record (a valid NCBI pattern for repeatable attributes), the values are
joined with a ``|`` pipe separator.

**Attribute collision on schema columns (``_dup_<field>`` keys):** A different
encoding is used when the *same standard schema column* receives two values
from two separate ``<Attribute>`` elements on the same BioSample record. The
first value is stored in the schema column as usual. The second (and any
further) value is stored in ``_extra_attributes`` under the key
``_dup_<standard_key>`` — for example ``_dup_isolation_source`` or
``_dup_host``. Multiple overflow values for the same field are themselves
pipe-joined. This convention is the reliable way to detect and retrieve all
submitted values for any schema field:

.. code-block:: python

   import json

   ea = json.loads(row["_extra_attributes"] or "{}")
   # Primary value:
   primary = row["isolation_source"]
   # Any duplicates:
   duplicates = ea.get("_dup_isolation_source", "").split("|")

The antibiogram list (if present) is stored under the ``"antibiogram"`` key as
a native Python list before the single ``json.dumps(extras)`` call serializes
the whole dict.

.. _thread-safety:

Thread-Safety
-------------

``ENTREZ_EMAIL``, ``ENTREZ_API_KEY``, and ``CACHE_DIR`` are **module-level
globals**. All three functions :func:`set_email`, :func:`set_api_key`, and
:func:`set_cache_dir`, as well as :func:`ingest` itself (which calls all three
setters internally when their corresponding parameters are supplied), write
these globals **without locking**.

**Consequence:** concurrent calls to ``ingest()`` from different threads with
different credentials will silently overwrite each other's state. This can
produce requests that use the wrong email or API key without raising any error.

**Recommended pattern for parallel workloads:** use a
``multiprocessing.Pool`` (each subprocess has its own memory space) rather
than a ``threading.Thread`` pool. If threads are required, serialize all calls
to ``ingest()`` with a ``threading.Lock``.
