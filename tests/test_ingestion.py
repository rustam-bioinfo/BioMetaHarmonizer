import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from unittest.mock import patch, MagicMock
from src.biometaharmonizer.ingestion import (
    _load_ids,
    _classify_ids,
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

    def test_returns_list_of_dicts(self):
        records = _parse_biosample_xml(MINIMAL_BIOSAMPLE_XML)
        assert isinstance(records, list)
        assert len(records) == 2

    def test_accession_extracted(self):
        records = _parse_biosample_xml(MINIMAL_BIOSAMPLE_XML)
        assert records[0]["biosample_accession"] == "SAMN12345678"
        assert records[1]["biosample_accession"] == "SAMN87654321"

    def test_harmonized_name_takes_priority(self):
        records = _parse_biosample_xml(MINIMAL_BIOSAMPLE_XML)
        assert "collection_date" in records[0]
        assert records[0]["collection_date"] == "2021-05-12"

    def test_fallback_to_attribute_name(self):
        records = _parse_biosample_xml(MINIMAL_BIOSAMPLE_XML)
        assert "isolation_source" in records[0]
        assert records[0]["isolation_source"] == "blood"

    def test_missing_attribute_is_absent(self):
        records = _parse_biosample_xml(MINIMAL_BIOSAMPLE_XML)
        assert "host" not in records[1]

    def test_empty_xml_returns_empty_list(self):
        empty_xml = b"""<?xml version="1.0"?><BioSampleSet></BioSampleSet>"""
        records = _parse_biosample_xml(empty_xml)
        assert records == []


# ─── ingest() integration (mocked) ───────────────────────────────────────────

class TestIngest:

    @patch("src.biometaharmonizer.ingestion._fetch_biosample_metadata")
    def test_samn_list_bypasses_resolution(self, mock_fetch, tmp_samn_file):
        mock_fetch.return_value = pd.DataFrame({"biosample_accession": ["SAMN12345678"]})
        result = ingest(tmp_samn_file)
        mock_fetch.assert_called_once()
        called_ids = mock_fetch.call_args[0][0]
        assert "SAMN12345678" in called_ids

    @patch("src.biometaharmonizer.ingestion._fetch_biosample_metadata")
    @patch("src.biometaharmonizer.ingestion._resolve_assembly_to_biosample")
    def test_gcx_list_triggers_resolution(self, mock_resolve, mock_fetch, tmp_gcx_file):
        mock_resolve.return_value = ["SAMN12345678", "SAMN87654321", "SAMN11111111"]
        mock_fetch.return_value = pd.DataFrame({"biosample_accession": ["SAMN12345678"]})
        ingest(tmp_gcx_file)
        mock_resolve.assert_called_once()

    @patch("src.biometaharmonizer.ingestion._fetch_biosample_metadata")
    @patch("src.biometaharmonizer.ingestion._resolve_assembly_to_biosample")
    def test_mixed_file_routes_both(self, mock_resolve, mock_fetch, tmp_mixed_file):
        mock_resolve.return_value = ["SAMN99999999"]
        mock_fetch.return_value = pd.DataFrame({"biosample_accession": ["SAMN12345678"]})
        ingest(tmp_mixed_file)
        mock_resolve.assert_called_once()
        mock_fetch.assert_called_once()

    @patch("src.biometaharmonizer.ingestion._fetch_biosample_metadata")
    @patch("src.biometaharmonizer.ingestion._resolve_assembly_to_biosample")
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
        with patch("src.biometaharmonizer.ingestion._resolve_assembly_to_biosample", return_value=[]):
            with pytest.raises(ValueError, match="No valid BioSample IDs"):
                ingest(f)
