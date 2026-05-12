.. _output:

======
Output
======

Module: :mod:`biometaharmonizer.output`

The output module provides two public functions for persisting the harmonized
DataFrame: :func:`~biometaharmonizer.output.write` for the main data file
and :func:`~biometaharmonizer.output.write_summary` for a fill-rate report.

Supported Formats
-----------------

The following format identifiers are accepted by :func:`~biometaharmonizer.output.write`
(case-insensitive). The full list is defined in the module-level constant
``_VALID_FORMATS = ("csv", "tsv", "excel", "parquet", "jsonl")``.

.. list-table:: 
   :header-rows: 1

   * - Format
     - Description
     - Engine used
   * - ``csv``
     - Comma-separated values, UTF-8 encoded.
     - pandas ``to_csv``
   * - ``tsv``
     - Tab-separated values, UTF-8 encoded. Useful for downstream shell processing.
     - pandas ``to_csv``
   * - ``excel``
     - Excel ``.xlsx`` workbook.
     - openpyxl engine
   * - ``parquet``
     - Apache Parquet columnar format.
     - pyarrow engine
   * - ``jsonl``
     - JSON Lines output, one JSON object per line.
     - custom line writer using ``json.dumps``

The ``write()`` function creates any missing parent directories automatically
via ``Path.mkdir(parents=True, exist_ok=True)``. It logs the output path,
record count, and column count at INFO level.

write() Signature
-----------------

.. code-block:: python

   from biometaharmonizer.output import write

   def write(df: pd.DataFrame, path, fmt: str = "csv") -> Path:
       ...

- **df** — the harmonized DataFrame returned by ``ingest()``.
- **path** — destination file path as a ``str`` or ``Path`` object.
- **fmt** — output format; default ``"csv"``. Case-insensitive.
- **Returns** the resolved absolute ``Path`` of the written file.
- **Raises** ``ValueError`` if ``fmt`` is not one of the five valid formats.

write_summary() Signature
--------------------------

.. code-block:: python

   from biometaharmonizer.output import write_summary

   def write_summary(df: pd.DataFrame, path) -> Path:
       ...

Writes a fill-rate summary CSV to ``path``. The output has three columns:

.. list-table:: 
   :header-rows: 1

   * - Column
     - Type
     - Description
   * - ``column_name``
     - str
     - Name of the source DataFrame column.
   * - ``non_null_count``
     - | int
     - Count of non-null values in the column.
   * - ``fill_pct``
     - float
     - Percentage of non-null rows (0–100.0).

Expanding ``_extra_attributes``
---------------------------------

The ``_extra_attributes`` column contains a JSON string representing a dict
of overflow attributes. To expand it into separate columns:

.. code-block:: python

   import json
   import pandas as pd

   # Parse the JSON strings
   ea = df["_extra_attributes"].dropna().apply(json.loads)

   # Normalize to a wide DataFrame
   ea_wide = pd.json_normalize(ea)
   ea_wide.index = df["_extra_attributes"].dropna().index

   # Join back to the main DataFrame (drop the original column)
   df_expanded = df.drop(columns=["_extra_attributes"]).join(ea_wide)

.. note::

   Columns from ``_extra_attributes`` are not part of the fixed schema and
   their names depend on the NCBI packages present in the input. Common
   keys include ``panel_id``, ``submission_contact``, ``submission_owner``,
   and ``antibiogram``.

Unnesting the Antibiogram
--------------------------

To convert the nested ``antibiogram`` list into a long-format table with one
row per antibiotic entry:

.. code-block:: python

   import json
   import pandas as pd

   abg_rows = []
   for _, row in df.iterrows():
       if pd.isna(row["_extra_attributes"]):
           continue
       ea = json.loads(row["_extra_attributes"])
       for entry in ea.get("antibiogram", []):
           entry["biosample_accession"] = row["biosample_accession"]
           abg_rows.append(entry)

   abg_df = pd.DataFrame(abg_rows)

Parquet Output and Downstream Pipelines
-----------------------------------------

Parquet is the recommended format for downstream Snakemake or Nextflow
pipelines because:

- Column types are preserved (no implicit string coercion).
- The ``_extra_attributes`` column stores the JSON string compactly.
- Files can be read by ``pandas``, ``polars``, ``dask``, and ``Apache Spark``.

To write Parquet:

.. code-block:: python

   bmh.write(df, "harmonized.parquet", fmt="parquet")

To read in a downstream rule:

.. code-block:: python

   import pandas as pd
   df = pd.read_parquet("harmonized.parquet")