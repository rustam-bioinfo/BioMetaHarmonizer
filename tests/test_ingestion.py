"""
Tests for ingestion.py.

All tests use synthetic XML -- no live NCBI calls are made.
"""

import json
import xml.etree.ElementTree as ET

import pytest

from biometaharmonizer.ingestion import _parse_antibiogram, _parse_biosample_xml


# ---------------------------------------------------------------------------
# Minimal synthetic BioSample XML that matches the real NCBI pathogen package
# structure for antibiogram data (verified against SAMN04014961).
#
# Key structural facts:
#   - <Table class="Antibiogram.1.0"> is nested inside <Description><Comment>
#   - Column names come from <Header><Cell> elements (human-readable labels)
#   - Data rows are in <Body><Row>, one <Cell> per column, positional
#   - Empty cells (<Cell/>) must be excluded from the output dict
#   - Cells whose text matches _NULL_PATTERNS must also be excluded
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
