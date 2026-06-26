"""Unit tests for ingest helper functions — no Azure credentials needed."""

import datetime
from unittest.mock import MagicMock, patch

import pytest
import requests

from function_app import (
    _download_and_upload,
    _parse_last_sync,
    _retry,
    _to_blob_metadata_value,
    _to_blob_name,
    _upload_changed_files,
)


def _make_streamed_response(chunks=(b"data",)):
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.raise_for_status = MagicMock()
    response.iter_content.return_value = list(chunks)
    return response


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

    def test_none_returns_custom_default(self):
        custom_default = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        assert _parse_last_sync(None, default=custom_default) == custom_default

    def test_empty_string_returns_custom_default(self):
        custom_default = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        assert _parse_last_sync("", default=custom_default) == custom_default

    def test_unrecognized_returns_custom_default(self):
        custom_default = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        assert _parse_last_sync("not-a-date", default=custom_default) == custom_default

    def test_valid_value_ignores_default(self):
        custom_default = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        result = _parse_last_sync("2024-06-01T12:00:00Z", default=custom_default)
        assert result.year == 2024 and result.month == 6 and result.day == 1


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

        def content_get(url, headers, stream, timeout):
            if "/items/2/content" in url:
                raise RuntimeError("Graph 403")
            return _make_streamed_response()

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

        def content_get(url, headers, stream, timeout):
            return _make_streamed_response()

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

        def content_get(url, headers, stream, timeout):
            return _make_streamed_response()

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

    def test_empty_drive_returns_zero_uploads(self):
        patches = self._patch_common([], lambda *a, **k: _make_streamed_response())
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
            )
            assert uploaded == 0
            assert earliest_success is None
        finally:
            for p in patches:
                p.stop()

    def test_already_synced_file_is_skipped(self):
        last_sync = datetime.datetime(2024, 6, 5, tzinfo=datetime.timezone.utc)
        items = [
            _make_item("1", "old.pdf", "2024-06-01T00:00:00Z"),
            _make_item("2", "also-old.pdf", "2024-06-05T00:00:00Z"),
        ]

        def content_get(url, headers, stream, timeout):
            raise AssertionError("should not download an already-synced file")

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
                last_sync=last_sync,
                headers={},
            )
            assert uploaded == 0
            assert earliest_success is None
        finally:
            for p in patches:
                p.stop()


class TestRetry:
    def test_succeeds_after_failures(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise requests.exceptions.RequestException("boom")
            return "ok"

        with patch("function_app.time.sleep") as mock_sleep:
            result = _retry(flaky, attempts=3, base_delay=1.0)

        assert result == "ok"
        assert calls["n"] == 3
        assert mock_sleep.call_count == 2

    def test_raises_after_exhausting_attempts(self):
        def always_fails():
            raise requests.exceptions.RequestException("boom")

        with patch("function_app.time.sleep") as mock_sleep:
            with pytest.raises(requests.exceptions.RequestException):
                _retry(always_fails, attempts=3, base_delay=1.0)

        assert mock_sleep.call_count == 2

    def test_non_matching_exception_propagates_immediately(self):
        calls = {"n": 0}

        def fails_with_other_error():
            calls["n"] += 1
            raise ValueError("not retryable")

        with patch("function_app.time.sleep") as mock_sleep:
            with pytest.raises(ValueError):
                _retry(fails_with_other_error, attempts=3, base_delay=1.0)

        assert calls["n"] == 1
        mock_sleep.assert_not_called()


class TestDownloadAndUpload:
    def test_happy_path_streams_chunks_into_upload_blob(self):
        response = _make_streamed_response(chunks=[b"abc", b"def"])
        blob_client = MagicMock()
        with patch("function_app.requests.get", return_value=response) as mock_get:
            _download_and_upload("http://example/content", {}, blob_client, {"k": "v"})

        mock_get.assert_called_once_with(
            "http://example/content", headers={}, stream=True, timeout=120
        )
        blob_client.upload_blob.assert_called_once()
        args, kwargs = blob_client.upload_blob.call_args
        assert list(args[0]) == [b"abc", b"def"]
        assert kwargs["overwrite"] is True
        assert kwargs["metadata"] == {"k": "v"}

    def test_request_exception_propagates(self):
        with patch("function_app.requests.get", side_effect=requests.exceptions.RequestException("boom")):
            blob_client = MagicMock()
            with pytest.raises(requests.exceptions.RequestException):
                _download_and_upload("http://example/content", {}, blob_client, None)
