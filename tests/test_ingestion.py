import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from unittest.mock import patch, MagicMock
from biometaharmonizer.ingestion import (
    _load_ids,
    _classify_ids,
    _deduplicate,
    _parse_biosample_xml,
    ingest,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_samn_file(tmp_path):
    f = tmp_path / "samn_ids.txt"
    f.write_text("SAMN12345678\nSAMN87654321\nSAMN11111111\n")
    return f


@pytest.fixture
def tmp_gcx_file(tmp_path):
    f = tmp_path / "gcx_ids.txt"
    f.write_text("GCF_000001405.40\nGCA_029710825.1\nGCF_002356575.1\n")
    return f


@pytest.fixture
def tmp_mixed_file(tmp_path):
    f = tmp_path / "mixed_ids.txt"
    f.write_text("GCF_000001405.40\nSAMN12345678\nGCA_029710825.1\nSAMD99999999\nJUNK_ID\n")
    return f


# Full XML fixture exercising every structural block the new parser extracts.
FULL_BIOSAMPLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<BioSampleSet>
  <BioSample accession="SAMN12345678" id="12345678"
             submission_date="2021-01-10T00:00:00.000"
             last_update="2022-06-15T00:00:00.000"
             publication_date="2021-02-01T00:00:00.000"
             access="public">
    <Ids>
      <Id db="BioSample" db_label="Sample name">MySample_001</Id>
      <Id db="SRA">SRS012345</Id>
      <Id db="BioProject">PRJNA654321</Id>
    </Ids>
    <Description>
      <Title>Klebsiella pneumoniae clinical isolate</Title>
      <Comment>
        <Paragraph>Isolated from blood of hospitalised patient in Moscow.</Paragraph>
      </Comment>
    </Description>
    <Organism taxonomy_id="573" taxonomy_name="Klebsiella pneumoniae">
      <OrganismName>Klebsiella pneumoniae</OrganismName>
    </Organism>
    <Package>Pathogen.cl.1.0</Package>
    <Status status="live" when="2021-02-01T00:00:00.000"/>
    <Attributes>
      <Attribute attribute_name="collection_date" harmonized_name="collection_date">2021-05-12</Attribute>
      <Attribute attribute_name="geo_loc_name" harmonized_name="geo_loc_name">Russia: Moscow</Attribute>
      <Attribute attribute_name="isolation_source">blood</Attribute>
      <Attribute attribute_name="host" harmonized_name="host">Homo sapiens</Attribute>
      <Attribute attribute_name="empty_attr" harmonized_name="empty_attr"></Attribute>
    </Attributes>
  </BioSample>
  <BioSample accession="SAMN87654321" id="87654321"
             submission_date="2019-03-01T00:00:00.000"
             last_update="2020-01-01T00:00:00.000"
             publication_date="2019-04-01T00:00:00.000"
             access="public">
    <Ids>
      <Id db="SRA">SRS099999</Id>
    </Ids>
    <Description>
      <Title>Bacillus cereus food isolate</Title>
    </Description>
    <Organism taxonomy_id="1396" taxonomy_name="Bacillus cereus">
      <OrganismName>Bacillus cereus</OrganismName>
    </Organism>
    <Package>Pathogen.env.1.0</Package>
    <Status status="live" when="2019-04-01T00:00:00.000"/>
    <Attributes>
      <Attribute attribute_name="collection_date" harmonized_name="collection_date">2019</Attribute>
      <Attribute attribute_name="geo_loc_name" harmonized_name="geo_loc_name">USA: California</Attribute>
    </Attributes>
  </BioSample>
  <BioSample accession="SAMN99999999" id="99999999">
  </BioSample>
</BioSampleSet>
"""

# Legacy minimal XML kept to test backward-compat attribute parsing paths.
MINIMAL_BIOSAMPLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<BioSampleSet>
  <BioSample accession="SAMN12345678" id="12345678">
    <Attributes>
      <Attribute attribute_name="collection_date" harmonized_name="collection_date">2021-05-12</Attribute>
      <Attribute attribute_name="geo_loc_name" harmonized_name="geo_loc_name">Russia: Moscow</Attribute>
      <Attribute attribute_name="isolation_source">blood</Attribute>
      <Attribute attribute_name="host" harmonized_name="host">Homo sapiens</Attribute>
    </Attributes>
  </BioSample>
  <BioSample accession="SAMN87654321" id="87654321">
    <Attributes>
      <Attribute attribute_name="collection_date" harmonized_name="collection_date">2019</Attribute>
      <Attribute attribute_name="geo_loc_name" harmonized_name="geo_loc_name">USA: California</Attribute>
    </Attributes>
  </BioSample>
</BioSampleSet>
"""


# ─── _load_ids ────────────────────────────────────────────────────────────────

class TestLoadIds:

    def test_load_from_file(self, tmp_samn_file):
        ids = _load_ids(tmp_samn_file)
        assert len(ids) == 3
        assert ids[0] == "SAMN12345678"

    def test_load_from_list(self):
        ids = _load_ids(["SAMN12345678", "GCF_000001405.40"])
        assert len(ids) == 2

    def test_empty_lines_stripped(self, tmp_path):
        f = tmp_path / "ids.txt"
        f.write_text("SAMN12345678\n\n  \nSAMN87654321\n")
        ids = _load_ids(f)
        assert len(ids) == 2

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            _load_ids("/nonexistent/path/ids.txt")

    def test_empty_list_input(self):
        ids = _load_ids([])
        assert ids == []


# ─── _classify_ids ───────────────────────────────────────────────────────────

class TestClassifyIds:

    def test_pure_samn(self):
        gcx, samn, unk = _classify_ids(["SAMN12345678", "SAMN87654321"])
        assert gcx == []
        assert len(samn) == 2
        assert unk == []

    def test_pure_gcx(self):
        gcx, samn, unk = _classify_ids(["GCF_000001405.40", "GCA_029710825.1"])
        assert len(gcx) == 2
        assert samn == []
        assert unk == []

    def test_mixed_input(self):
        ids = ["GCF_000001405.40", "SAMN12345678", "SAMD99999999", "SAME00000001", "JUNK"]
        gcx, samn, unk = _classify_ids(ids)
        assert len(gcx) == 1
        assert len(samn) == 3
        assert unk == ["JUNK"]

    def test_all_unrecognized(self):
        gcx, samn, unk = _classify_ids(["JUNK1", "JUNK2"])
        assert gcx == [] and samn == []
        assert len(unk) == 2

    def test_empty_input(self):
        gcx, samn, unk = _classify_ids([])
        assert gcx == [] and samn == [] and unk == []

    def test_case_insensitive_samn(self):
        gcx, samn, unk = _classify_ids(["samn12345678"])
        assert len(samn) == 1


# ─── _parse_biosample_xml ────────────────────────────────────────────────────

class TestParseBiosampleXml:

    # --- basic structure ---

    def test_returns_list_of_dicts(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert isinstance(records, list)
        assert len(records) == 3

    def test_empty_xml_returns_empty_list(self):
        empty_xml = b"""<?xml version="1.0"?><BioSampleSet></BioSampleSet>"""
        records = _parse_biosample_xml(empty_xml)
        assert records == []

    # --- top-level BioSample element attributes ---

    def test_accession_extracted(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["biosample_accession"] == "SAMN12345678"
        assert records[1]["biosample_accession"] == "SAMN87654321"

    def test_biosample_id_extracted(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["biosample_id"] == "12345678"

    def test_submission_date_extracted(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["submission_date"] == "2021-01-10T00:00:00.000"

    def test_last_update_extracted(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["last_update"] == "2022-06-15T00:00:00.000"

    def test_publication_date_extracted(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["publication_date"] == "2021-02-01T00:00:00.000"

    def test_access_extracted(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["access"] == "public"

    # --- <Ids> block ---

    def test_sra_accession_extracted(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["sra_accession"] == "SRS012345"
        assert records[1]["sra_accession"] == "SRS099999"

    def test_bioproject_accession_extracted(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["bioproject_accession"] == "PRJNA654321"

    def test_sample_name_id_extracted(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["sample_name_id"] == "MySample_001"

    # --- <Organism> block ---

    def test_taxonomy_id_extracted(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["taxonomy_id"] == "573"
        assert records[1]["taxonomy_id"] == "1396"

    def test_taxonomy_name_extracted(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["taxonomy_name"] == "Klebsiella pneumoniae"

    def test_organism_name_extracted(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["organism_name"] == "Klebsiella pneumoniae"

    # --- <Description> block ---

    def test_title_extracted(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["title"] == "Klebsiella pneumoniae clinical isolate"
        assert records[1]["title"] == "Bacillus cereus food isolate"

    def test_description_comment_extracted(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert "Moscow" in records[0]["description_comment"]

    def test_description_comment_absent_is_none(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[1]["description_comment"] is None

    # --- <Package> block ---

    def test_ncbi_package_extracted(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["ncbi_package"] == "Pathogen.cl.1.0"
        assert records[1]["ncbi_package"] == "Pathogen.env.1.0"

    # --- <Status> block ---

    def test_status_extracted(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["status"] == "live"

    def test_status_date_extracted(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["status_date"] == "2021-02-01T00:00:00.000"

    # --- None-safety for absent blocks ---

    def test_absent_organism_returns_none_fields(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        bare = records[2]
        assert bare["taxonomy_id"] is None
        assert bare["taxonomy_name"] is None
        assert bare["organism_name"] is None

    def test_absent_status_returns_none_fields(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        bare = records[2]
        assert bare["status"] is None
        assert bare["status_date"] is None

    def test_absent_ids_block_leaves_no_sra(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        bare = records[2]
        assert bare.get("sra_accession") is None or "sra_accession" not in bare

    # --- <Attributes> block ---

    def test_harmonized_name_takes_priority(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["collection_date"] == "2021-05-12"

    def test_fallback_to_attribute_name(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0]["isolation_source"] == "blood"

    def test_empty_attribute_value_stored_as_none(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert records[0].get("empty_attr") is None

    def test_missing_attribute_is_absent_from_record(self):
        records = _parse_biosample_xml(FULL_BIOSAMPLE_XML)
        assert "host" not in records[1]

    # --- backward compat: minimal XML without structural blocks ---

    def test_minimal_xml_still_parses_accession(self):
        records = _parse_biosample_xml(MINIMAL_BIOSAMPLE_XML)
        assert records[0]["biosample_accession"] == "SAMN12345678"

    def test_minimal_xml_structural_fields_are_none(self):
        records = _parse_biosample_xml(MINIMAL_BIOSAMPLE_XML)
        assert records[0]["taxonomy_id"] is None
        assert records[0]["status"] is None
        assert records[0]["organism_name"] is None


# ─── ingest() integration (mocked) ───────────────────────────────────────────

class TestIngest:

    @patch("biometaharmonizer.ingestion._fetch_biosample_metadata")
    def test_samn_list_bypasses_resolution(self, mock_fetch, tmp_samn_file):
        mock_fetch.return_value = pd.DataFrame({"biosample_accession": ["SAMN12345678"]})
        result = ingest(tmp_samn_file)
        mock_fetch.assert_called_once()
        called_ids = mock_fetch.call_args[0][0]
        assert "SAMN12345678" in called_ids

    @patch("biometaharmonizer.ingestion._fetch_biosample_metadata")
    @patch("biometaharmonizer.ingestion._resolve_assembly_to_biosample")
    def test_gcx_list_triggers_resolution(self, mock_resolve, mock_fetch, tmp_gcx_file):
        mock_resolve.return_value = ["SAMN12345678", "SAMN87654321", "SAMN11111111"]
        mock_fetch.return_value = pd.DataFrame({"biosample_accession": ["SAMN12345678"]})
        ingest(tmp_gcx_file)
        mock_resolve.assert_called_once()

    @patch("biometaharmonizer.ingestion._fetch_biosample_metadata")
    @patch("biometaharmonizer.ingestion._resolve_assembly_to_biosample")
    def test_mixed_file_routes_both(self, mock_resolve, mock_fetch, tmp_mixed_file):
        mock_resolve.return_value = ["SAMN99999999"]
        mock_fetch.return_value = pd.DataFrame({"biosample_accession": ["SAMN12345678"]})
        ingest(tmp_mixed_file)
        mock_resolve.assert_called_once()
        mock_fetch.assert_called_once()

    @patch("biometaharmonizer.ingestion._fetch_biosample_metadata")
    @patch("biometaharmonizer.ingestion._resolve_assembly_to_biosample")
    def test_unrecognized_ids_trigger_warning(self, mock_resolve, mock_fetch, tmp_mixed_file, capsys):
        mock_resolve.return_value = ["SAMN99999999"]
        mock_fetch.return_value = pd.DataFrame()
        ingest(tmp_mixed_file)
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "unrecognized" in captured.out.lower()

    def test_all_unrecognized_raises(self, tmp_path):
        f = tmp_path / "junk.txt"
        f.write_text("JUNK1\nJUNK2\n")
        with patch("biometaharmonizer.ingestion._resolve_assembly_to_biosample", return_value=[]):
            with pytest.raises(ValueError, match="No valid BioSample IDs"):
                ingest(f)


# --- _deduplicate ---

class TestDeduplicate:

    def test_removes_duplicates(self):
        ids = ["SAMN001", "SAMN002", "SAMN001", "SAMN003"]
        result = _deduplicate(ids)
        assert result == ["SAMN001", "SAMN002", "SAMN003"]

    def test_preserves_order(self):
        ids = ["SAMN003", "SAMN001", "SAMN002"]
        result = _deduplicate(ids)
        assert result == ["SAMN003", "SAMN001", "SAMN002"]

    def test_no_duplicates_unchanged(self):
        ids = ["SAMN001", "SAMN002"]
        result = _deduplicate(ids)
        assert result == ids


# --- Live NCBI test (requires network) ---

@pytest.mark.network
def test_live_ncbi_fetch():
    """
    Integration test that calls NCBI Entrez for real.
    Skipped by default -- run with: pytest -m network
    """
    pytest.skip("Live NCBI test skipped by default; run with -m network")
