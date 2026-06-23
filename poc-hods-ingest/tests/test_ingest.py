"""Unit tests for ingest helper functions — no Azure credentials needed."""

import datetime
from unittest.mock import MagicMock, patch

import pytest

from function_app import (
    _parse_last_sync,
    _to_blob_metadata_value,
    _to_blob_name,
    _upload_changed_files,
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


def _make_item(item_id, name, modified):
    return {"id": item_id, "name": name, "file": {}, "lastModifiedDateTime": modified}


class TestUploadChangedFiles:
    """_upload_changed_files reports the earliest modified_at among files it
    actually uploaded, so the caller can advance last-sync only that far
    instead of to now_utc — files left over when max_files is hit are
    retried on the next run instead of being permanently skipped (ISSUE-3),
    and the max_files cap is no longer fixed at 5 (ISSUE-2)."""

    def _patch_common(self, items, content_side_effect):
        return [
            patch("function_app._get_drive_list_id", return_value="list-1"),
            patch("function_app._get_lookup_column_info", return_value=None),
            patch("function_app._list_all_items", return_value=items),
            patch("function_app._fetch_item_fields", return_value={}),
            patch("function_app.requests.get", side_effect=content_side_effect),
        ]

    def test_item_failure_propagates_without_returning_partial_result(self):
        items = [
            _make_item("1", "a.pdf", "2024-06-01T00:00:00Z"),
            _make_item("2", "b.pdf", "2024-06-02T00:00:00Z"),
            _make_item("3", "c.pdf", "2024-06-03T00:00:00Z"),
        ]

        def content_get(url, headers, timeout):
            if "/items/2/content" in url:
                raise RuntimeError("Graph 403")
            response = MagicMock()
            response.content = b"data"
            response.raise_for_status = MagicMock()
            return response

        patches = self._patch_common(items, content_get)
        for p in patches:
            p.start()
        try:
            blob_service_client = MagicMock()
            with pytest.raises(RuntimeError):
                _upload_changed_files(
                    blob_service_client=blob_service_client,
                    container_name="ingest-output",
                    drive_id="drive-1",
                    site_id="site-1",
                    last_sync=datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc),
                    headers={},
                )
        finally:
            for p in patches:
                p.stop()

    def test_max_files_caps_uploads(self):
        items = [_make_item(str(i), f"{i}.pdf", "2024-06-0%dT00:00:00Z" % (i + 1)) for i in range(3)]

        def content_get(url, headers, timeout):
            response = MagicMock()
            response.content = b"data"
            response.raise_for_status = MagicMock()
            return response

        patches = self._patch_common(items, content_get)
        for p in patches:
            p.start()
        try:
            blob_service_client = MagicMock()
            uploaded, earliest_success = _upload_changed_files(
                blob_service_client=blob_service_client,
                container_name="ingest-output",
                drive_id="drive-1",
                site_id="site-1",
                last_sync=datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc),
                headers={},
                max_files=2,
            )
            assert uploaded == 2
            assert earliest_success.day == 1
        finally:
            for p in patches:
                p.stop()

    def test_default_max_files_allows_more_than_five(self):
        items = [_make_item(str(i), f"{i}.pdf", "2024-06-0%dT00:00:00Z" % (i + 1)) for i in range(7)]

        def content_get(url, headers, timeout):
            response = MagicMock()
            response.content = b"data"
            response.raise_for_status = MagicMock()
            return response

        patches = self._patch_common(items, content_get)
        for p in patches:
            p.start()
        try:
            blob_service_client = MagicMock()
            uploaded, _ = _upload_changed_files(
                blob_service_client=blob_service_client,
                container_name="ingest-output",
                drive_id="drive-1",
                site_id="site-1",
                last_sync=datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc),
                headers={},
            )
            assert uploaded == 7
        finally:
            for p in patches:
                p.stop()
