"""
Tests for ingestion.py.

All tests use synthetic XML -- no live NCBI calls are made.
"""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import pytest

from biometaharmonizer.ingestion import (
    BIOSAMPLE_SCHEMA,
    BIOSAMPLE_SCHEMA_SET,
    _classify_ids,
    _deduplicate,
    _load_ids,
    _normalize_null,
    _parse_antibiogram,
    _parse_biosample_xml,
    set_email,
)


# ---------------------------------------------------------------------------
# Synthetic XML fixtures
# ---------------------------------------------------------------------------

_ANTIBIOGRAM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<BioSampleSet>
  <BioSample access="public"
             publication_date="2015-08-27T00:00:00.000"
             last_update="2017-06-08T07:50:03.371"
             submission_date="2015-08-27T16:16:20.000"
             id="4014961"
             accession="SAMN04014961">
    <Ids>
      <Id db="BioSample" is_primary="1">SAMN04014961</Id>
      <Id db_label="Sample name">FDA-CDC-AR_0120</Id>
    </Ids>
    <Description>
      <Title>Pathogen: clinical or host-associated sample from Klebsiella pneumoniae</Title>
      <Organism taxonomy_id="573" taxonomy_name="Klebsiella pneumoniae">
        <OrganismName>Klebsiella pneumoniae</OrganismName>
      </Organism>
      <Comment>
        <Table class="Antibiogram.1.0">
          <Caption>Antibiogram</Caption>
          <Header>
            <Cell>Antibiotic</Cell>
            <Cell>Resistance phenotype</Cell>
            <Cell>Measurement sign</Cell>
            <Cell>Measurement</Cell>
            <Cell>Measurement units</Cell>
            <Cell>Laboratory typing method</Cell>
            <Cell>Laboratory typing platform</Cell>
            <Cell>Vendor</Cell>
            <Cell>Laboratory typing method version or reagent</Cell>
            <Cell>Testing standard</Cell>
          </Header>
          <Body>
            <Row>
              <Cell>amikacin</Cell>
              <Cell>resistant</Cell>
              <Cell>==</Cell>
              <Cell>64</Cell>
              <Cell>mg/L</Cell>
              <Cell>MIC</Cell>
              <Cell/>
              <Cell/>
              <Cell/>
              <Cell>CLSI</Cell>
            </Row>
            <Row>
              <Cell>meropenem</Cell>
              <Cell>resistant</Cell>
              <Cell>&gt;</Cell>
              <Cell>16</Cell>
              <Cell>mg/L</Cell>
              <Cell>MIC</Cell>
              <Cell/>
              <Cell/>
              <Cell/>
              <Cell>CLSI</Cell>
            </Row>
            <Row>
              <Cell>tigecycline</Cell>
              <Cell>susceptible</Cell>
              <Cell>==</Cell>
              <Cell>0.5</Cell>
              <Cell>mg/L</Cell>
              <Cell>MIC</Cell>
              <Cell/>
              <Cell/>
              <Cell/>
              <Cell>CLSI</Cell>
            </Row>
          </Body>
        </Table>
      </Comment>
    </Description>
    <Owner>
      <Name>Centers for Disease Control and Prevention</Name>
    </Owner>
    <Package>Pathogen.cl.1.0</Package>
    <Attributes>
      <Attribute attribute_name="collected_by" harmonized_name="collected_by">FDA-CDC</Attribute>
      <Attribute attribute_name="strain" harmonized_name="strain">AR_0120</Attribute>
    </Attributes>
    <Status status="live" when="2015-08-27T16:16:20.000"/>
  </BioSample>
</BioSampleSet>
"""

_NO_ANTIBIOGRAM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<BioSampleSet>
  <BioSample access="public"
             publication_date="2020-01-01T00:00:00.000"
             last_update="2020-01-01T00:00:00.000"
             submission_date="2020-01-01T00:00:00.000"
             id="99999999"
             accession="SAMN99999999">
    <Ids>
      <Id db="BioSample" is_primary="1">SAMN99999999</Id>
    </Ids>
    <Description>
      <Title>Standard microbe sample</Title>
      <Organism taxonomy_id="1" taxonomy_name="Bacterium">
        <OrganismName>Bacterium</OrganismName>
      </Organism>
    </Description>
    <Package>Microbe.1.0</Package>
    <Attributes>
      <Attribute attribute_name="isolation_source" harmonized_name="isolation_source">soil</Attribute>
    </Attributes>
    <Status status="live" when="2020-01-01T00:00:00.000"/>
  </BioSample>
</BioSampleSet>
"""

_FULL_IDS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<BioSampleSet>
  <BioSample access="public"
             publication_date="2021-01-01T00:00:00.000"
             last_update="2021-01-01T00:00:00.000"
             submission_date="2021-01-01T00:00:00.000"
             id="12345678"
             accession="SAMN12345678">
    <Ids>
      <Id db="BioSample" is_primary="1">SAMN12345678</Id>
      <Id db="SRA">SRR9999999</Id>
      <Id db="BioProject">PRJNA999999</Id>
      <Id db_label="Sample name">MySampleName</Id>
    </Ids>
    <Description>
      <Title>Full IDs test sample</Title>
      <Organism taxonomy_id="9606" taxonomy_name="Homo sapiens">
        <OrganismName>Homo sapiens</OrganismName>
      </Organism>
      <Comment>
        <Paragraph>This is a test comment.</Paragraph>
      </Comment>
    </Description>
    <Package>Human.1.0</Package>
    <Attributes>
      <Attribute attribute_name="host" harmonized_name="host">Homo sapiens</Attribute>
    </Attributes>
    <Status status="live" when="2021-01-01T12:00:00.000"/>
  </BioSample>
</BioSampleSet>
"""

_OWNER_CONTACT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<BioSampleSet>
  <BioSample access="public"
             publication_date="2022-01-01T00:00:00.000"
             last_update="2022-01-01T00:00:00.000"
             submission_date="2022-01-01T00:00:00.000"
             id="11111111"
             accession="SAMN11111111">
    <Ids>
      <Id db="BioSample" is_primary="1">SAMN11111111</Id>
    </Ids>
    <Description>
      <Title>Owner contact test</Title>
      <Organism taxonomy_id="1" taxonomy_name="Bacterium">
        <OrganismName>Bacterium</OrganismName>
      </Organism>
    </Description>
    <Owner>
      <Name>Test University</Name>
      <Contacts>
        <Contact>
          <First>Jane</First>
          <Last>Doe</Last>
        </Contact>
      </Contacts>
    </Owner>
    <Package>Microbe.1.0</Package>
    <Attributes/>
    <Status status="live" when="2022-01-01T00:00:00.000"/>
  </BioSample>
</BioSampleSet>
"""

_COLLISION_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<BioSampleSet>
  <BioSample access="public"
             publication_date="2023-01-01T00:00:00.000"
             last_update="2023-01-01T00:00:00.000"
             submission_date="2023-01-01T00:00:00.000"
             id="22222222"
             accession="SAMN22222222">
    <Ids>
      <Id db="BioSample" is_primary="1">SAMN22222222</Id>
    </Ids>
    <Description>
      <Title>Collision test</Title>
      <Organism taxonomy_id="1" taxonomy_name="Bacterium">
        <OrganismName>Bacterium</OrganismName>
      </Organism>
    </Description>
    <Package>Microbe.1.0</Package>
    <Attributes>
      <Attribute attribute_name="strain" harmonized_name="strain">strain_A</Attribute>
      <Attribute attribute_name="strain" harmonized_name="strain">strain_B</Attribute>
      <Attribute attribute_name="weird_custom_key">custom_value</Attribute>
    </Attributes>
    <Status status="live" when="2023-01-01T00:00:00.000"/>
  </BioSample>
</BioSampleSet>
"""

_ANTIBIOGRAM_ZERO_ROWS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<BioSampleSet>
  <BioSample accession="SAMN00000001" id="1"
             access="public"
             submission_date="2020-01-01T00:00:00.000"
             last_update="2020-01-01T00:00:00.000"
             publication_date="2020-01-01T00:00:00.000">
    <Description>
      <Comment>
        <Table class="Antibiogram.1.0">
          <Header>
            <Cell>Antibiotic</Cell>
            <Cell>Resistance phenotype</Cell>
          </Header>
          <Body/>
        </Table>
      </Comment>
    </Description>
  </BioSample>
</BioSampleSet>
"""

_ANTIBIOGRAM_EMPTY_HEADER_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<BioSampleSet>
  <BioSample accession="SAMN00000002" id="2"
             access="public"
             submission_date="2020-01-01T00:00:00.000"
             last_update="2020-01-01T00:00:00.000"
             publication_date="2020-01-01T00:00:00.000">
    <Description>
      <Comment>
        <Table class="Antibiogram.1.0">
          <Header/>
          <Body>
            <Row><Cell>amikacin</Cell></Row>
          </Body>
        </Table>
      </Comment>
    </Description>
  </BioSample>
</BioSampleSet>
"""

_ANTIBIOGRAM_NULL_CELL_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<BioSampleSet>
  <BioSample accession="SAMN00000003" id="3"
             access="public"
             submission_date="2020-01-01T00:00:00.000"
             last_update="2020-01-01T00:00:00.000"
             publication_date="2020-01-01T00:00:00.000">
    <Description>
      <Comment>
        <Table class="Antibiogram.1.0">
          <Header>
            <Cell>Antibiotic</Cell>
            <Cell>Resistance phenotype</Cell>
            <Cell>Testing standard</Cell>
          </Header>
          <Body>
            <Row>
              <Cell>amikacin</Cell>
              <Cell>not provided</Cell>
              <Cell>CLSI</Cell>
            </Row>
          </Body>
        </Table>
      </Comment>
    </Description>
  </BioSample>
</BioSampleSet>
"""


# ---------------------------------------------------------------------------
# TestNormalizeNull
# ---------------------------------------------------------------------------

class TestNormalizeNull:
    def test_none_returns_none(self):
        assert _normalize_null(None) is None

    def test_pd_na_returns_none(self):
        assert _normalize_null(pd.NA) is None

    def test_empty_string_returns_none(self):
        assert _normalize_null("") is None

    def test_whitespace_only_returns_none(self):
        assert _normalize_null("   ") is None

    @pytest.mark.parametrize("raw", [
        "missing", "misssing", "missng", "mising",
        "unknown", "unkown", "unknwon", "unknow",
        "NA", "N/A", "na", "n/a",
        "none", "null", "nil",
        "not provided", "not collected", "not applicable",
        "not available", "not determined", "not recorded",
        "not given", "not stated", "not specified",
        "unavailable", "unspecified", "undetermined",
        "restricted", "restricted access", "withheld",
        "tbd", "tba",
    ])
    def test_null_patterns_return_none(self, raw):
        assert _normalize_null(raw) is None

    @pytest.mark.parametrize("raw", [
        "missing: field not available",
        "not applicable: pre-2023",
        "data agreement established pre-2023",
        "data agreement established pre2023",
    ])
    def test_prefix_patterns_return_none(self, raw):
        assert _normalize_null(raw) is None

    def test_case_insensitive(self):
        assert _normalize_null("MISSING") is None
        assert _normalize_null("Unknown") is None

    def test_valid_value_returned_unchanged(self):
        assert _normalize_null("soil") == "soil"
        assert _normalize_null("  soil  ") == "soil"
        assert _normalize_null("Klebsiella pneumoniae") == "Klebsiella pneumoniae"


# ---------------------------------------------------------------------------
# TestLoadIds
# ---------------------------------------------------------------------------

class TestLoadIds:
    def test_list_input(self):
        ids = _load_ids(["SAMN00000001", "SAMN00000002", ""])
        assert ids == ["SAMN00000001", "SAMN00000002"]

    def test_file_input(self, tmp_path):
        f = tmp_path / "ids.txt"
        f.write_text("SAMN00000001\nSAMN00000002\n\nSAMN00000003\n")
        ids = _load_ids(str(f))
        assert ids == ["SAMN00000001", "SAMN00000002", "SAMN00000003"]

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _load_ids(str(tmp_path / "nonexistent.txt"))


# ---------------------------------------------------------------------------
# TestDeduplicate
# ---------------------------------------------------------------------------

class TestDeduplicate:
    def test_removes_duplicates(self):
        result = _deduplicate(["A", "B", "A", "C", "B"])
        assert result == ["A", "B", "C"]

    def test_preserves_order(self):
        result = _deduplicate(["C", "A", "B", "A"])
        assert result == ["C", "A", "B"]

    def test_no_duplicates_unchanged(self):
        result = _deduplicate(["X", "Y", "Z"])
        assert result == ["X", "Y", "Z"]

    def test_empty_list(self):
        assert _deduplicate([]) == []


# ---------------------------------------------------------------------------
# TestClassifyIds
# ---------------------------------------------------------------------------

class TestClassifyIds:
    def test_gcf_goes_to_gcx(self):
        gcx, samn, unrecog = _classify_ids(["GCF_000001405.40"])
        assert gcx == ["GCF_000001405.40"]
        assert samn == []
        assert unrecog == []

    def test_gca_goes_to_gcx(self):
        gcx, samn, unrecog = _classify_ids(["GCA_000001405.40"])
        assert gcx == ["GCA_000001405.40"]

    def test_samn_goes_to_samn(self):
        gcx, samn, unrecog = _classify_ids(["SAMN12345678"])
        assert samn == ["SAMN12345678"]
        assert gcx == []

    def test_same_goes_to_samn(self):
        _, samn, _ = _classify_ids(["SAME123456"])
        assert samn == ["SAME123456"]

    def test_samd_goes_to_samn(self):
        _, samn, _ = _classify_ids(["SAMD123456"])
        assert samn == ["SAMD123456"]

    def test_unrecognised_prefix(self):
        gcx, samn, unrecog = _classify_ids(["ERR123456"])
        assert unrecog == ["ERR123456"]
        assert gcx == []
        assert samn == []

    def test_mixed_list(self):
        ids = ["SAMN00001", "GCF_000001", "WEIRD_001", "SAMD99999"]
        gcx, samn, unrecog = _classify_ids(ids)
        assert "GCF_000001" in gcx
        assert "SAMN00001" in samn
        assert "SAMD99999" in samn
        assert "WEIRD_001" in unrecog


# ---------------------------------------------------------------------------
# TestSetEmail
# ---------------------------------------------------------------------------

class TestSetEmail:
    def test_valid_email_accepted(self):
        set_email("researcher@example.org")

    @pytest.mark.parametrize("bad", [
        "notanemail",
        "missing_at_sign.com",
        "@nodomain",
        "",
    ])
    def test_invalid_format_raises(self, bad):
        with pytest.raises(ValueError, match="Invalid email"):
            set_email(bad)

    @pytest.mark.parametrize("placeholder", [
        "your@email.com",
        "example@example.com",
        "user@example.org",
        "test@test.com",
        "email@example.com",
    ])
    def test_placeholder_email_raises(self, placeholder):
        with pytest.raises(ValueError, match="placeholder"):
            set_email(placeholder)


# ---------------------------------------------------------------------------
# TestBioSampleSchema
# ---------------------------------------------------------------------------

class TestBioSampleSchema:
    def test_geo_loc_raw_absent(self):
        """Regression: geo_loc_raw was removed; must not reappear in schema."""
        assert "geo_loc_raw" not in BIOSAMPLE_SCHEMA

    def test_schema_column_count(self):
        assert len(BIOSAMPLE_SCHEMA) == 57

    def test_schema_set_consistent_with_list(self):
        assert BIOSAMPLE_SCHEMA_SET == set(BIOSAMPLE_SCHEMA)

    def test_no_duplicate_columns(self):
        assert len(BIOSAMPLE_SCHEMA) == len(set(BIOSAMPLE_SCHEMA))

    def test_required_columns_present(self):
        for col in (
            "biosample_accession", "organism_name", "geo_loc_name", "lat_lon",
            "collection_date", "isolation_source", "host",
            "one_health_category", "_extra_attributes",
        ):
            assert col in BIOSAMPLE_SCHEMA_SET


# ---------------------------------------------------------------------------
# TestParseBioSampleXmlIds
# ---------------------------------------------------------------------------

class TestParseBioSampleXmlIds:
    def test_sra_accession_parsed(self):
        records = _parse_biosample_xml(_FULL_IDS_XML)
        assert records[0]["sra_accession"] == "SRR9999999"

    def test_bioproject_from_id_element(self):
        records = _parse_biosample_xml(_FULL_IDS_XML)
        assert records[0]["bioproject_accession"] == "PRJNA999999"

    def test_sample_name_id_parsed(self):
        records = _parse_biosample_xml(_FULL_IDS_XML)
        assert records[0]["sample_name_id"] == "MySampleName"

    def test_biosample_accession_from_attribute(self):
        records = _parse_biosample_xml(_FULL_IDS_XML)
        assert records[0]["biosample_accession"] == "SAMN12345678"


# ---------------------------------------------------------------------------
# TestParseBioSampleXmlMeta
# ---------------------------------------------------------------------------

class TestParseBioSampleXmlMeta:
    def test_description_comment_parsed(self):
        records = _parse_biosample_xml(_FULL_IDS_XML)
        assert records[0]["description_comment"] == "This is a test comment."

    def test_status_and_status_date_parsed(self):
        records = _parse_biosample_xml(_FULL_IDS_XML)
        rec = records[0]
        assert rec["status"] == "live"
        assert rec["status_date"] == "2021-01-01T12:00:00.000"

    def test_collected_by_from_owner_name(self):
        """When collected_by attribute is absent, Owner/Name fills collected_by."""
        records = _parse_biosample_xml(_OWNER_CONTACT_XML)
        assert records[0]["collected_by"] == "Test University"

    def test_submission_contact_from_owner_contacts(self):
        records = _parse_biosample_xml(_OWNER_CONTACT_XML)
        extras = json.loads(records[0]["_extra_attributes"])
        assert extras["submission_contact"] == "Jane Doe"


# ---------------------------------------------------------------------------
# TestParseBioSampleXmlExtras
# ---------------------------------------------------------------------------

class TestParseBioSampleXmlExtras:
    def test_unknown_attribute_stored_in_extras(self):
        records = _parse_biosample_xml(_COLLISION_XML)
        extras = json.loads(records[0]["_extra_attributes"])
        assert extras["weird_custom_key"] == "custom_value"

    def test_attribute_collision_first_value_kept(self):
        records = _parse_biosample_xml(_COLLISION_XML)
        assert records[0]["strain"] == "strain_A"

    def test_attribute_collision_duplicate_in_extras(self):
        records = _parse_biosample_xml(_COLLISION_XML)
        extras = json.loads(records[0]["_extra_attributes"])
        assert extras["_dup_strain"] == "strain_B"


# ---------------------------------------------------------------------------
# TestParseBioSampleXmlSynonym
# ---------------------------------------------------------------------------

class TestParseBioSampleXmlSynonym:
    def test_synonym_resolves_attribute_name_to_schema_column(self):
        """synonym_lookup maps a non-standard attribute_name to a schema column."""
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<BioSampleSet>
  <BioSample access="public"
             publication_date="2024-01-01T00:00:00.000"
             last_update="2024-01-01T00:00:00.000"
             submission_date="2024-01-01T00:00:00.000"
             id="33333333"
             accession="SAMN33333333">
    <Ids><Id db="BioSample" is_primary="1">SAMN33333333</Id></Ids>
    <Description>
      <Title>Synonym test</Title>
      <Organism taxonomy_id="1" taxonomy_name="Bacterium">
        <OrganismName>Bacterium</OrganismName>
      </Organism>
    </Description>
    <Package>Microbe.1.0</Package>
    <Attributes>
      <Attribute attribute_name="geographic_location">Russia: Moscow</Attribute>
    </Attributes>
    <Status status="live" when="2024-01-01T00:00:00.000"/>
  </BioSample>
</BioSampleSet>
"""
        synonym_lookup = {"geographic_location": "geo_loc_name"}
        records = _parse_biosample_xml(xml, synonym_lookup=synonym_lookup)
        assert records[0]["geo_loc_name"] == "Russia: Moscow"


# ---------------------------------------------------------------------------
# TestParseBioSampleXmlEdge
# ---------------------------------------------------------------------------

class TestParseBioSampleXmlEdge:
    def test_empty_bytes_returns_empty_list(self):
        assert _parse_biosample_xml(b"") == []

    def test_whitespace_only_returns_empty_list(self):
        assert _parse_biosample_xml(b"   ") == []

    def test_malformed_xml_returns_empty_list(self):
        assert _parse_biosample_xml(b"<broken") == []

    def test_geo_loc_raw_not_in_record_keys(self):
        """geo_loc_raw was removed from schema; must not appear as a key."""
        records = _parse_biosample_xml(_NO_ANTIBIOGRAM_XML)
        assert "geo_loc_raw" not in records[0]

    def test_output_columns_match_schema(self):
        """Every key in a parsed record must be in BIOSAMPLE_SCHEMA."""
        records = _parse_biosample_xml(_NO_ANTIBIOGRAM_XML)
        for key in records[0]:
            assert key in BIOSAMPLE_SCHEMA_SET, f"unexpected key: {key!r}"


# ---------------------------------------------------------------------------
# TestParseAntibiogram (original + extended)
# ---------------------------------------------------------------------------

class TestParseAntibiogram:
    def _sample_elem(self, xml_bytes):
        root = ET.fromstring(xml_bytes)
        return root.find(".//BioSample")

    def test_returns_list_when_present(self):
        rows = _parse_antibiogram(self._sample_elem(_ANTIBIOGRAM_XML))
        assert isinstance(rows, list)
        assert len(rows) == 3

    def test_first_row_fields(self):
        rows = _parse_antibiogram(self._sample_elem(_ANTIBIOGRAM_XML))
        first = rows[0]
        assert first["antibiotic_name"] == "amikacin"
        assert first["resistance_phenotype"] == "resistant"
        assert first["measurement_sign"] == "=="
        assert first["measurement"] == "64"
        assert first["measurement_units"] == "mg/L"
        assert first["laboratory_typing_method"] == "MIC"
        assert first["testing_standard"] == "CLSI"

    def test_empty_cells_excluded(self):
        rows = _parse_antibiogram(self._sample_elem(_ANTIBIOGRAM_XML))
        for row in rows:
            assert "laboratory_typing_platform" not in row
            assert "vendor" not in row
            assert "laboratory_typing_method_version_or_reagent" not in row

    def test_measurement_sign_gt_decoded(self):
        rows = _parse_antibiogram(self._sample_elem(_ANTIBIOGRAM_XML))
        meropenem = next(r for r in rows if r["antibiotic_name"] == "meropenem")
        assert meropenem["measurement_sign"] == ">"

    def test_returns_none_when_absent(self):
        assert _parse_antibiogram(self._sample_elem(_NO_ANTIBIOGRAM_XML)) is None

    def test_zero_row_body_returns_none(self):
        """A table with a valid header but no data rows must return None."""
        assert _parse_antibiogram(self._sample_elem(_ANTIBIOGRAM_ZERO_ROWS_XML)) is None

    def test_empty_header_returns_none(self):
        """A table with an empty <Header> must return None before reading rows."""
        assert _parse_antibiogram(self._sample_elem(_ANTIBIOGRAM_EMPTY_HEADER_XML)) is None

    def test_null_pattern_cell_excluded_from_row(self):
        """A cell value matching _NULL_PATTERNS must be excluded from the row dict."""
        rows = _parse_antibiogram(self._sample_elem(_ANTIBIOGRAM_NULL_CELL_XML))
        assert rows is not None
        assert len(rows) == 1
        assert "resistance_phenotype" not in rows[0]
        assert rows[0]["antibiotic_name"] == "amikacin"
        assert rows[0]["testing_standard"] == "CLSI"


# ---------------------------------------------------------------------------
# TestParseBioSampleXmlAntibiogram (original)
# ---------------------------------------------------------------------------

class TestParseBioSampleXmlAntibiogram:
    def test_antibiogram_stored_in_extra_attributes(self):
        records = _parse_biosample_xml(_ANTIBIOGRAM_XML)
        assert len(records) == 1
        rec = records[0]
        assert rec["biosample_accession"] == "SAMN04014961"
        assert rec["_extra_attributes"] is not None
        extras = json.loads(rec["_extra_attributes"])
        assert "antibiogram" in extras

    def test_antibiogram_is_list_not_string(self):
        """antibiogram must be a native list inside _extra_attributes JSON,
        not a double-encoded string-within-JSON."""
        records = _parse_biosample_xml(_ANTIBIOGRAM_XML)
        extras = json.loads(records[0]["_extra_attributes"])
        rows = extras["antibiogram"]
        assert isinstance(rows, list), (
            f"expected list, got {type(rows).__name__}: {rows!r}"
        )
        assert len(rows) == 3
        assert rows[0]["antibiotic_name"] == "amikacin"

    def test_no_antibiogram_key_for_standard_package(self):
        records = _parse_biosample_xml(_NO_ANTIBIOGRAM_XML)
        assert len(records) == 1
        extras_raw = records[0]["_extra_attributes"]
        if extras_raw is not None:
            extras = json.loads(extras_raw)
            assert "antibiogram" not in extras

    def test_standard_fields_still_parsed(self):
        records = _parse_biosample_xml(_ANTIBIOGRAM_XML)
        rec = records[0]
        assert rec["strain"] == "AR_0120"
        assert rec["ncbi_package"] == "Pathogen.cl.1.0"
        assert rec["organism_name"] == "Klebsiella pneumoniae"
        assert rec["taxonomy_id"] == "573"
        assert rec["status"] == "live"
