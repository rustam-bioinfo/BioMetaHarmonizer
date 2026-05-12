.. _faq:

===
FAQ
===

.. rubric:: 1. How do I register and use an NCBI API key?

Create a free NCBI account at https://www.ncbi.nlm.nih.gov/account/, navigate
to **Settings → API Key Management**, and click **Create an API Key**. Copy
the generated string and pass it to :func:`~biometaharmonizer.ingestion.ingest`
via the ``api_key`` argument, or register it globally with
:func:`~biometaharmonizer.ingestion.set_api_key`:

.. code-block:: python

   import biometaharmonizer as bmh
   bmh.set_api_key("YOUR_KEY")
   df = bmh.ingest(source="ids.txt", email="your@email.com")

Without a key, NCBI allows 3 requests/second (``inter_batch_sleep = 0.34 s``);
with a key the limit rises to 10 requests/second (``inter_batch_sleep = 0.12 s``).

.. rubric:: 2. What happens when an accession is suppressed or withdrawn?

NCBI returns no ``<BioSample>`` XML element for suppressed, withdrawn, or
otherwise invalid accessions. The record is silently absent from the output
DataFrame. When assembly accessions cannot be resolved through either the
local index or the Entrez elink fallback, a WARNING is logged:

.. code-block:: text

   WARNING biometaharmonizer.ingestion:
     NOT resolved (suppressed/invalid after both passes): 2
     Unresolved: ['GCF_000000001.1', 'SAMN00000000']

The count of output rows will be less than the count of input IDs. Compare
``len(df)`` with your input size to detect suppressed accessions.

.. rubric:: 3. How do I handle the rate-limit error HTTP 429?

An HTTP 429 response from NCBI means too many requests were sent in the
last second. BioMetaHarmonizer's retry logic handles 429 errors **separately**
from other transient errors: instead of the normal exponential backoff
(2 s, 4 s, 8 s), it applies a **flat 60-second wait** (``_HTTP_429_WAIT_S``)
before each retry regardless of attempt number. This longer wait is
necessary because NCBI's rate-limiting window takes time to expire.

If 429 errors persist even after retries:

- Ensure you have registered and passed an NCBI API key (raises limit from
  3 to 10 requests/second).
- Reduce ``fetch_batch_size`` (e.g. to 100) to send smaller requests.
- Avoid running multiple concurrent instances against the same API key.

.. note::

   The fuzzy-matching fallback in
   :class:`~biometaharmonizer.one_health.OneHealthClassifier` requires
   ``rapidfuzz>=3.0.0``. If it is not installed the classifier operates
   without fuzzy matching and logs a WARNING. All other features are
   unaffected. Install it with ``pip install rapidfuzz>=3.0.0``.

.. rubric:: 4. What does ``refresh_cache=True`` do and when should I use it?

When ``refresh_cache=True`` is set, the assembly summary flat files in
``CACHE_DIR`` (``assembly_summary_refseq.txt`` and
``assembly_summary_genbank.txt``) are deleted and re-downloaded
unconditionally — regardless of their age — before the run begins. Use this
flag when NCBI has added new assemblies since your last run and you need the
latest assembly→BioSample mappings:

.. code-block:: python

   df = bmh.ingest(source="new_assemblies.txt",
                   email="your@email.com",
                   refresh_cache=True)

Without this flag, cached files are used for up to 7 days (``_CACHE_TTL_DAYS``).

.. rubric:: 5. Why do some records have ``None`` in most columns?

Records with many ``None`` values fall into two categories:

1. **Minimal BioSample records:** the submitter only provided the mandatory
   fields. For Pathogen packages this is common; for environmental packages
   MIxS fields dominate while clinical fields are absent.
2. **Null normalization:** BioMetaHarmonizer applies ``_normalize_null()`` to
   every attribute value. Any value matching ``_NULL_PATTERNS`` (e.g.
   ``"missing"``, ``"N/A"``, ``"not provided"``, ``"unknown"``) is stored as
   ``None``. This is intentional: downstream tools can use ``df.notna()`` to
   compute genuine fill rates.

Use :func:`~biometaharmonizer.output.write_summary` to generate a fill-rate
report and identify which columns are sparsely populated for your dataset.

.. rubric:: 6. How do I use BioMetaHarmonizer in Google Colab?

Override the cache directory to a location inside ``/content`` so that the
~200 MB assembly index files are stored on the Colab writable filesystem
(or in a mounted Google Drive):

.. code-block:: console

   !pip install biometaharmonizer

   import biometaharmonizer as bmh

   bmh.set_cache_dir("/content/bmh_cache")       # or "/content/drive/MyDrive/bmh_cache"
   bmh.set_email("your@email.com")
   bmh.set_api_key("YOUR_KEY")

   df = bmh.ingest(source=["SAMN02436525", "SAMN02434874"])
   df.to_csv("harmonized.csv", index=False)

The module docstring of ``ingestion.py`` contains this exact note under
*Working directory note (Colab)*.

.. rubric:: 7. What is the difference between ``fetch_batch_size`` and ``esearch_batch_size``?

- **``fetch_batch_size``** (default 200; clamped at 500) — controls how many
  BioSample records are retrieved per ``efetch`` HTTP request. Each
  ``efetch`` call returns raw XML for ``N`` records; larger batches mean
  fewer round trips but larger per-response payloads.

- **``esearch_batch_size``** (module constant ``_ESEARCH_BATCH`` = 100) — controls
  how many accession strings are assembled into a single ``esearch`` term
  (e.g. ``"SAMN01[Accession] OR SAMN02[Accession] OR ..."``) before a server-side
  History slot is created. It is also used in the Entrez elink fallback path
  for resolving GCF/GCA accessions that are not found in the local index.

In the CLI, ``--esearch-batch-size`` defaults to 200 (overriding the module
constant). The Python API default is 100.

.. rubric:: 8. How do I access antibiogram data from the output DataFrame?

Antibiogram data is stored as a JSON list inside the ``_extra_attributes``
column. To extract it:

.. code-block:: python

   import json, pandas as pd

   def get_antibiogram(row):
       if pd.isna(row["_extra_attributes"]):
           return []
       ea = json.loads(row["_extra_attributes"])
       return ea.get("antibiogram", [])

   abg_rows = []
   for _, row in df.iterrows():
       for entry in get_antibiogram(row):
           entry["biosample_accession"] = row["biosample_accession"]
           abg_rows.append(entry)

   abg_df = pd.DataFrame(abg_rows)

See :ref:`antibiogram` for a complete walkthrough.

.. rubric:: 9. How do I filter only records with a known ``one_health_category``?

The ``one_health_category`` column is always a string (never NaN). Records
that could not be classified receive the string ``"Unclassified"``. To filter
for classified records:

.. code-block:: python

   classified = df[df["one_health_category"] != "Unclassified"]

To filter for a specific category:

.. code-block:: python

   human = df[df["one_health_category"] == "Human"]
   food  = df[df["one_health_category"] == "Food"]

.. rubric:: 10. Why does ``_extra_attributes`` contain pipe-separated values for some keys?

NCBI BioSample allows submitters to include multiple ``<Attribute>`` elements
with the same ``attribute_name`` on a single record (a repeatable-attribute
pattern used for AMR fields, multiple culture collection numbers, etc.).
When this occurs, the ingestion parser joins them with ``|`` before storing
in ``extras[key]``. This ensures no data is lost while keeping the JSON
representation compact. To split them back:

.. code-block:: python

   import json

   ea = json.loads(row["_extra_attributes"])
   for key, val in ea.items():
       if isinstance(val, str) and "|" in val:
           parts = val.split("|")

.. rubric:: 11. How do I input assembly accessions vs. BioSample accessions?

BioMetaHarmonizer automatically detects the accession type by prefix:

- ``SAMN``, ``SAME``, ``SAMD`` → BioSample accessions (direct fetch)
- ``GCF_``, ``GCA_`` → Assembly accessions (two-step resolution)

You can mix both types in the same input file or list. Unrecognized IDs
(wrong prefix) are logged at WARNING level and skipped. See
:ref:`ingestion` for the full two-step resolution process for assembly
accessions.

.. rubric:: 12. How do I rebuild the assembly index cache after NCBI adds new genomes?

Pass ``refresh_cache=True`` to ``ingest()`` or use ``--refresh-cache`` on the
CLI. This forces deletion and re-download of both assembly summary files
from NCBI FTP before processing begins, regardless of how recently they were
last downloaded:

.. code-block:: bash

   biometaharmonizer run \
       --input assemblies.txt \
       --email your@email.com \
       --output harmonized.csv \
       --refresh-cache

The files (~100 MB each) are stored in ``CACHE_DIR``
(``~/.biometaharmonizer/cache/`` by default).

.. rubric:: 13. When and how do I re-run ``build_dictionaries.py`` to refresh the One Health term dictionary?

Re-run ``scripts/build_dictionaries.py`` when:

- NCBI taxonomy is updated and new host/organism names are needed.
- New OLS ontology versions are released.
- You add new hand-curated entries to the base ``one_health_dictionaries.json``
  and need to propagate collision detection.
- New One Health categories are required.

To rebuild in place (overwrites the bundled file):

.. code-block:: bash

   python scripts/build_dictionaries.py \
       --base   src/biometaharmonizer/schemas/one_health_dictionaries.json \
       --output src/biometaharmonizer/schemas/one_health_dictionaries.json

The base dictionary is loaded first; hand-curated entries are never overwritten
(``base_wins`` strategy).

.. rubric:: 14. How do I use ``build_dictionaries.py`` with a pre-downloaded ``taxdmp.zip``?

To avoid the ~65 MB automatic download from NCBI FTP, download
``taxdmp.zip`` once and pass its local path via ``--taxdmp``:

.. code-block:: bash

   # Download once:
   wget https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdmp.zip

   # Use the local file on subsequent runs:
   python scripts/build_dictionaries.py \
       --taxdmp /path/to/taxdmp.zip \
       --output src/biometaharmonizer/schemas/one_health_dictionaries.json

You may also pass the path to an already-extracted directory that contains
``names.dmp`` and ``nodes.dmp``:

.. code-block:: bash

   python scripts/build_dictionaries.py \
       --taxdmp /path/to/extracted_taxdmp/ \
       --output src/biometaharmonizer/schemas/one_health_dictionaries.json

.. rubric:: 15. Is BioMetaHarmonizer thread-safe?

**No.** ``ENTREZ_EMAIL``, ``ENTREZ_API_KEY``, and ``CACHE_DIR`` are
module-level globals shared across all threads in the same Python process.
Calling :func:`~biometaharmonizer.ingestion.set_email`,
:func:`~biometaharmonizer.ingestion.set_api_key`,
:func:`~biometaharmonizer.ingestion.set_cache_dir`, or
:func:`~biometaharmonizer.ingestion.ingest` concurrently from multiple
threads **will silently overwrite credentials and configuration** without
raising any error. Requests may be sent with the wrong email or API key.

**Recommended patterns:**

- Use a ``multiprocessing.Pool`` — each subprocess has an independent memory
  space and its own copy of the globals.
- Serialize all ``ingest()`` calls with a ``threading.Lock`` if threads are
  required.
- When running batch jobs, process each job in a separate OS process.
