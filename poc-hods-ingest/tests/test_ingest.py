"""Unit tests for ingest helper functions — no Azure credentials needed."""

import datetime

import pytest

from function_app import (
    _parse_last_sync,
    _to_blob_metadata_value,
    _to_blob_name,
)


class TestParseLastSync:
    def test_none_returns_epoch(self):
        result = _parse_last_sync(None)
        assert result == datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)

    def test_empty_string_returns_epoch(self):
        assert _parse_last_sync("") == datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)

    def test_iso8601_z(self):
        result = _parse_last_sync("2024-06-01T12:00:00Z")
        assert result.year == 2024
        assert result.tzinfo is not None

    def test_iso8601_offset(self):
        result = _parse_last_sync("2024-06-01T12:00:00+00:00")
        assert result.year == 2024

    def test_legacy_format(self):
        result = _parse_last_sync("2024-06-01 12:00:00")
        assert result.year == 2024

    def test_unrecognized_returns_epoch(self):
        result = _parse_last_sync("not-a-date")
        assert result == datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


class TestToBlobName:
    def test_simple_name(self):
        assert _to_blob_name("document.pdf") == "document.pdf"

    def test_strips_path(self):
        assert _to_blob_name("/some/path/file.docx") == "file.docx"

    def test_replaces_spaces(self):
        assert _to_blob_name("my document.pdf") == "my_document.pdf"

    def test_empty_becomes_file(self):
        assert _to_blob_name("") == "file"


class TestToBlobMetadataValue:
    def test_string(self):
        assert _to_blob_metadata_value("hello") == "hello"

    def test_none(self):
        assert _to_blob_metadata_value(None) == ""

    def test_list_of_lookups(self):
        value = [{"LookupValue": "Bulletins"}, {"LookupValue": "Standards"}]
        result = _to_blob_metadata_value(value)
        assert "Bulletins" in result
        assert "Standards" in result

    def test_dict_lookup(self):
        result = _to_blob_metadata_value({"LookupValue": "AL"})
        assert result == "AL"

    def test_non_ascii_stripped(self):
        result = _to_blob_metadata_value("héllo")
        assert result == "hllo"
