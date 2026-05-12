.. _schema_reference:

================
Schema Reference
================

Every call to :func:`~biometaharmonizer.ingestion.ingest` returns a DataFrame
with exactly **57 columns** in the order defined by ``_load_final_schema()``.
All columns are initialized to ``None``/NaN for records that do not carry the
corresponding attribute.

Output Columns
--------------

.. list-table:: Output column schema
   :header-rows: 1

   * - Column
     - Type
     - Source
     - Description
     - Example
   * - ``biosample_accession``
     - str/NaN
     - BioSample XML ``@accession``
     - Primary INSDC BioSample accession.
     - ``SAMN02436525``
   * - ``biosample_id``
     - str/NaN
     - BioSample XML ``@id``
     - NCBI internal numeric BioSample ID.
     - ``2436525``
   * - ``sra_accession``
     - str/NaN
     - ``<Id db="SRA">``
     - Linked SRA experiment/run accession.
     - ``SRR1234567``
   * - ``bioproject_accession``
     - str/NaN
     - ``<Id db="BioProject">`` / assembly index
     - BioProject accession; back-filled from assembly index.
     - ``PRJNA123456``
   * - ``assembly_accession_refseq``
     - str/NaN
     - Assembly index (RefSeq)
     - RefSeq assembly accession for this BioSample.
     - ``GCF_000009045.1``
   * - ``assembly_accession_genbank``
     - str/NaN
     - Assembly index (GenBank)
     - GenBank assembly accession for this BioSample.
     - ``GCA_000009045.1``
   * - ``sample_name_id``
     - str/NaN
     - ``<Id db_label="Sample name">``
     - Submitter-assigned sample name/ID.
     - ``KP-2021-001``
   * - ``taxonomy_id``
     - str/NaN
     - ``<Organism @taxonomy_id>``
     - NCBI Taxonomy ID.
     - ``573``
   * - ``taxonomy_name``
     - str/NaN
     - ``<Organism @taxonomy_name>``
     - NCBI Taxonomy name (species-level label).
     - ``Klebsiella pneumoniae``
   * - ``organism_name``
     - str/NaN
     - ``<OrganismName>`` or fallback
     - Organism name as submitted; falls back to ``taxonomy_name``.
     - ``K. pneumoniae subsp.``
   * - ``isolate``
     - str/NaN
     - Attribute
     - Isolate identifier.
     - ``KP-2021-001``
   * - ``strain``
     - str/NaN
     - Attribute
     - Strain designation.
     - ``ATCC 700603``
   * - ``sub_strain``
     - str/NaN
     - Attribute
     - Sub-strain designation.
     - ``variant-A``
   * - ``serotype``
     - str/NaN
     - Attribute
     - Serotype (antigen type).
     - ``O1:K1``
   * - ``serovar``
     - str/NaN
     - Attribute
     - Serovar designation.
     - ``Typhimurium``
   * - ``genotype``
     - str/NaN
     - Attribute
     - Genotype classification.
     - ``ST258``
   * - ``culture_collection``
     - str/NaN
     - Attribute
     - Culture collection number/ID.
     - ``ATCC:700603``
   * - ``host``
     - str/NaN
     - Attribute
     - Host organism as submitted.
     - ``Homo sapiens``
   * - ``host_disease``
     - str/NaN
     - Attribute
     - Disease of the host.
     - ``pneumonia``
   * - ``host_age``
     - str/NaN
     - Attribute
     - Age of the host at time of sampling.
     - ``45``
   * - ``host_sex``
     - str/NaN
     - Attribute
     - Biological sex of the host.
     - ``male``
   * - ``host_tissue_sampled``
     - str/NaN
     - Attribute
     - Tissue or body site sampled.
     - ``lung``
   * - ``isolation_source``
     - str/NaN
     - Attribute
     - Physical, chemical, or biological material of sample.
     - ``blood``
   * - ``sample_type``
     - str/NaN
     - Attribute
     - Type of sample (e.g. clinical, environmental).
     - ``clinical``
   * - ``env_broad_scale``
     - str/NaN
     - Attribute (MIxS)
     - Broad-scale environmental context (MIxS field).
     - ``grassland biome``
   * - ``env_local_scale``
     - str/NaN
     - Attribute (MIxS)
     - Local environmental context (MIxS field).
     - ``pasture``
   * - ``env_medium``
     - str/NaN
     - Attribute (MIxS)
     - Environmental medium (MIxS field).
     - ``soil``
   * - ``geo_loc_name``
     - str/NaN
     - Attribute
     - Original ``geo_loc_name`` as submitted to NCBI.
     - ``Russia: Novosibirsk``
   * - ``lat_lon``
     - str/NaN
     - Attribute
     - Latitude/longitude as submitted (free-text string).
     - ``56.0153 N 92.8932 E``
   * - ``geo_country``
     - str/NaN
     - GeoEngine
     - Normalised country display name.
     - ``Russia``
   * - ``geo_region``
     - str/NaN
     - GeoEngine
     - Sub-national region as submitted.
     - ``Novosibirsk Oblast``
   * - ``geo_locality``
     - str/NaN
     - GeoEngine
     - Locality or sub-region as submitted.
     - ``Akademgorodok``
   * - ``geo_iso3166``
     - str/NaN
     - GeoEngine + pycountry
     - ISO 3166-1 alpha-2 code; ``"HISTORICAL"`` for defunct countries.
     - ``RU``
   * - ``geo_sea_ocean``
     - str/NaN
     - GeoEngine
     - Ocean, sea, gulf, bay, or other named water body for aquatic samples.
     - ``Pacific Ocean``
   * - ``collection_date``
     - str/NaN
     - Attribute + DateEngine
     - ISO 8601 point collection date (YYYY, YYYY-MM, YYYY-MM-DD).
     - ``2021-06-15``
   * - ``collection_date_range``
     - str/NaN
     - Attribute + DateEngine
     - Verbatim original date string for range/approximate inputs.
     - ``2020-01/2020-06``
   * - ``one_health_category``
     - str
     - OneHealthClassifier
     - One Health tier. Always a string; never NaN. Possible values: ``Human``, ``Animal``, ``Plant``, ``Food``, ``Environmental``, ``Unclassified``.
     - ``Human``
   * - ``one_health_term``
     - str/NaN
     - OneHealthClassifier
     - The matched term that drove the classification.
     - ``blood``
   * - ``one_health_confidence``
     - float
     - OneHealthClassifier
     - Numeric confidence score in the range [0.0, 1.0].
     - ``0.85``
   * - ``one_health_evidence_level``
     - str
     - OneHealthClassifier
     - Discretized confidence: ``high``, ``medium``, ``low``, or ``unresolved``.
     - ``high``
   * - ``one_health_processing``
     - str/NaN
     - OneHealthClassifier
     - Detected processing/preservation term (e.g. ``frozen``).
     - ``frozen``
   * - ``one_health_setting``
     - str/NaN
     - OneHealthClassifier
     - Detected setting term (e.g. ``hospital``).
     - ``hospital``
   * - ``one_health_source_field``
     - str/NaN
     - OneHealthClassifier
     - Name of the input field that provided the winning evidence, or
       ``"setting_inference"`` when the category was derived from a setting
       term with no direct biological evidence.
     - ``isolation_source``
   * - ``outbreak``
     - str/NaN
     - Attribute
     - Outbreak identifier or name.
     - ``2011 Germany HUS``
   * - ``sequencing_method``
     - str/NaN
     - Attribute
     - Sequencing platform or technology.
     - ``Illumina HiSeq 2500``
   * - ``assembly_method``
     - str/NaN
     - Attribute
     - Assembly software/method.
     - ``SPAdes v3.15``
   * - ``collected_by``
     - str/NaN
     - Attribute
     - Name of person/institution that collected the sample.
     - ``CDC``
   * - ``ncbi_package``
     - str/NaN
     - ``<Package>`` element
     - NCBI BioSample package name.
     - ``Pathogen.cl.1.0``
   * - ``submission_date``
     - str/NaN
     - BioSample XML ``@submission_date``
     - ISO 8601 date when the record was submitted.
     - ``2021-09-01``
   * - ``last_update``
     - str/NaN
     - BioSample XML ``@last_update``
     - ISO 8601 date of last record update.
     - ``2022-03-15``
   * - ``publication_date``
     - str/NaN
     - BioSample XML ``@publication_date``
     - ISO 8601 date when the record was made public.
     - ``2021-09-05``
   * - ``access``
     - str/NaN
     - BioSample XML ``@access``
     - Access level (e.g. ``"public"``).
     - ``public``
   * - ``status``
     - str/NaN
     - ``<Status @status>``
     - Record status (e.g. ``"live"``, ``"suppressed"``).
     - ``live``
   * - ``status_date``
     - str/NaN
     - ``<Status @when>``
     - ISO 8601 date of the most recent status change.
     - ``2021-09-05``
   * - ``title``
     - str/NaN
     - ``<Description/Title>``
     - BioSample title as submitted.
     - ``K. pneumoniae isolate``
   * - ``description_comment``
     - str/NaN
     - ``<Description/Comment/Paragraph>``
     - Free-text comment paragraph from the BioSample record.
     - ``Hospital-acquired...``
   * - ``_extra_attributes``
     - str/NaN
     - Overflow attributes
     - JSON string containing all attributes that did not resolve to a known final output column.
     - See below

_extra_attributes
-----------------

``_extra_attributes`` is a JSON-serialized dict. It captures all attribute
key-value pairs from the BioSample XML that do not map to any of the 56 named
schema columns via the synonym lookup.

**JSON structure:**

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
       }
     ],
     "panel_id": "TREKAMRO",
     "submission_contact": "John Smith",
     "submission_owner": "University Hospital Lab",
     "_dup_isolation_source": "wound swab"
   }

**Known sub-keys:**

.. list-table:: _extra_attributes sub-keys
   :header-rows: 1

   * - Sub-key
     - Value type
     - Description
   * - ``antibiogram``
     - list of dicts
     - Antibiogram rows; one dict per antibiotic. See :ref:`antibiogram` for full details.
   * - ``panel_id``
     - str
     - AMR panel identifier from NCBI Pathogen records.
   * - ``submission_contact``
     - str
     - Submitter contact name/email.
   * - ``submission_owner``
     - str
     - Submitting organization name.
   * - ``_dup_<field>``
     - str
     - **Attribute collision on a schema column.** When two ``<Attribute>``
       elements on the same BioSample record both resolve to the same standard
       schema column (e.g. two ``isolation_source`` attributes), the first
       value is stored in the schema column and any additional values are
       stored here under the key ``_dup_<standard_key>`` (e.g.
       ``_dup_isolation_source``). Multiple overflow values are joined with
       ``|``. This differs from the plain pipe-joining of truly extra
       (non-schema) attributes.
   * - (other attribute keys)
     - str
     - Any other attribute that did not resolve to a named schema column.
       Multiple values for the same key are joined with ``|``.

**Pipe-separated values:** When NCBI XML contains multiple ``<Attribute>``
elements with the same key on a single BioSample record, the values are
concatenated with a ``|`` pipe separator inside the JSON string. This is
an intentional design decision to preserve all submitted values without
data loss.

**Recovering duplicate schema-column values:**

.. code-block:: python

   import json

   ea = json.loads(row["_extra_attributes"] or "{}")
   # Primary isolation_source value:
   primary = row["isolation_source"]
   # Any duplicate isolation_source values submitted by the depositor:
   dups = ea.get("_dup_isolation_source", "").split("|")
