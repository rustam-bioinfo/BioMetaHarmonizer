.. _antibiogram:

===========
Antibiogram
===========

NCBI BioSample records from **Pathogen** packages (e.g.
``Pathogen.cl.1.0``, ``Pathogen.env.1.0``, ``Pathogen.cl.2.0``) may contain
drug-susceptibility testing data stored as an antibiogram table embedded in
the BioSample XML. BioMetaHarmonizer extracts this table automatically and
preserves it in the ``_extra_attributes`` column.

XML Structure
-------------

NCBI embeds the antibiogram as a generic HTML-like table nested inside the
``<Description>`` section of the ``<BioSample>`` element:

.. code-block:: none

   <BioSample accession="SAMN12345678" ...>
     <Description>
       <Comment>
         <Table class="Antibiogram.1.0">
           <Header>
             <Cell>Antibiotic</Cell>
             <Cell>Resistance Phenotype</Cell>
             <Cell>Measurement Sign</Cell>
             <Cell>Measurement</Cell>
             <Cell>Measurement Units</Cell>
             <Cell>Laboratory Typing Method</Cell>
             <Cell>Laboratory Typing Platform</Cell>
             <Cell>Vendor</Cell>
             <Cell>Laboratory Typing Method Version or Reagent</Cell>
             <Cell>Testing Standard</Cell>
           </Header>
           <Body>
             <Row>
               <Cell>ampicillin</Cell>
               <Cell>susceptible</Cell>
               <Cell>&lt;=</Cell>
               <Cell>8</Cell>
               <Cell>mg/L</Cell>
               <Cell>MIC</Cell>
               <Cell></Cell>
               <Cell></Cell>
               <Cell></Cell>
               <Cell>CLSI</Cell>
             </Row>
           </Body>
         </Table>
       </Comment>
     </Description>
   </BioSample>

The table is located via the XPath expression:

.. code-block:: python

   sample_elem.find('.//Comment/Table[@class="Antibiogram.1.0"]')

Parsed Columns
--------------

The ``_parse_antibiogram()`` function maps the 10 positional ``<Header><Cell>``
labels (lowercased) to canonical field names via ``_ANTIBIOGRAM_HEADER_MAP``:

.. list-table:: NCBI antibiogram header mapping
   :header-rows: 1
   :widths: 45 55

   * - NCBI Header Cell (lowercase)
     - Canonical field name
   * - ``antibiotic``
     - ``antibiotic_name``
   * - ``resistance phenotype``
     - ``resistance_phenotype``
   * - ``measurement sign``
     - ``measurement_sign``
   * - ``measurement``
     - ``measurement``
   * - ``measurement units``
     - ``measurement_units``
   * - ``laboratory typing method``
     - ``laboratory_typing_method``
   * - ``laboratory typing platform``
     - ``laboratory_typing_platform``
   * - ``vendor``
     - ``vendor``
   * - ``laboratory typing method version or reagent``
     - ``laboratory_typing_method_version_or_reagent``
   * - ``testing standard``
     - ``testing_standard``

Empty Cell Handling
-------------------

``_normalize_null()`` is applied to every ``<Cell>`` text value. Any empty,
whitespace-only, or null-pattern cell is excluded from the resulting row dict.
This means that ``laboratory_typing_platform``, ``vendor``, and
``laboratory_typing_method_version_or_reagent`` are **often absent** from
individual row dicts because NCBI submitters commonly leave them blank —
these three columns represent instrument/reagent details that most labs do
not report.

Storage in ``_extra_attributes``
---------------------------------

The parsed list of row dicts is assigned directly to
``extras["antibiogram"]`` as a **native Python list** (not a pre-serialized
JSON string). This design ensures that the single ``json.dumps(extras)`` call
at the end of ``_parse_biosample_xml`` encodes the entire ``_extra_attributes``
dict — including the antibiogram — in one pass without double-encoding.

The resulting JSON in ``_extra_attributes`` has the structure:

.. code-block:: json

   {
     "antibiogram": [
       {
         "antibiotic_name": "ampicillin",
         "resistance_phenotype": "susceptible",
         "measurement_sign": "<=",
         "measurement": "8",
         "measurement_units": "mg/L",
         "laboratory_typing_method": "MIC",
         "testing_standard": "CLSI"
       },
       {
         "antibiotic_name": "tetracycline",
         "resistance_phenotype": "resistant",
         "measurement_sign": ">",
         "measurement": "16",
         "measurement_units": "mg/L",
         "laboratory_typing_method": "MIC",
         "testing_standard": "CLSI"
       }
     ]
   }

Working with Antibiogram Data
-------------------------------

To extract and expand the antibiogram into a per-antibiotic long-format
DataFrame:

.. code-block:: python

   import json
   import pandas as pd
   import biometaharmonizer as bmh

   df = bmh.ingest(["SAMN12345678", "SAMN12345679"], email="your@email.com")

   # Step 1: filter records that have antibiogram data
   has_abg = df["_extra_attributes"].notna()
   df_abg = df[has_abg].copy()

   # Step 2: parse _extra_attributes JSON string
   df_abg["_ea_dict"] = df_abg["_extra_attributes"].apply(json.loads)

   # Step 3: keep only records with an antibiogram key
   df_abg = df_abg[
       df_abg["_ea_dict"].apply(lambda d: "antibiogram" in d)
   ]

   # Step 4: explode the antibiogram list to one row per antibiotic
   antibiogram_rows = []
   for _, row in df_abg.iterrows():
       for abg_entry in row["_ea_dict"]["antibiogram"]:
           abg_entry["biosample_accession"] = row["biosample_accession"]
           antibiogram_rows.append(abg_entry)

   abg_df = pd.DataFrame(antibiogram_rows)
   print(abg_df.columns.tolist())
   # Typical columns: biosample_accession, antibiotic_name,
   #   resistance_phenotype, measurement_sign, measurement,
   #   measurement_units, laboratory_typing_method, testing_standard

   # Alternatively, use pd.json_normalize for the same result:
   # abg_df = pd.json_normalize(
   #     df_abg["_ea_dict"].apply(lambda d: d.get("antibiogram", [])).explode()
   # )

Commonly Empty Columns
-----------------------

The following three columns are frequently absent from individual antibiogram
row dicts because most submitters do not provide them:

- ``laboratory_typing_platform`` — instrument/system used (e.g. ``"Sensititre"``)
- ``vendor`` — reagent/panel vendor (e.g. ``"Trek"``)
- ``laboratory_typing_method_version_or_reagent`` — reagent version or panel
  identifier (e.g. ``"TREKAMRO"``)

Code that iterates over antibiogram dicts should use ``.get()`` with a default
rather than direct key access.
