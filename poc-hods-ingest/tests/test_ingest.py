"""Unit tests for ingest helper functions — no Azure credentials needed."""

import datetime
import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from function_app import (
    _build_blob_metadata,
    _download_and_upload,
    _extract_purpose_and_scope,
    _fetch_content,
    _get_allowed_extensions,
    _get_delta_changes,
    _is_allowed_file_name,
    _is_system_field,
    _parse_last_sync,
    _read_delta_state,
    _retry,
    _sanitize_metadata_key,
    _save_delta_state,
    _to_blob_metadata_value,
    _to_blob_name,
    _to_utc_iso,
    _trim_metadata,
    _upload_changed_files,
    _upload_drive_items,
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

    def test_taxonomy_label_extracted(self):
        result = _to_blob_metadata_value({"Label": "Berlin", "TermGuid": "3fce150e-bd09-4075", "WssId": 5})
        assert result == "Berlin"

    def test_taxonomy_in_list(self):
        result = _to_blob_metadata_value([{"Label": "Berlin"}, {"Label": "Paris"}])
        assert "Berlin" in result
        assert "Paris" in result

    def test_dict_without_known_keys_falls_back_to_str(self):
        result = _to_blob_metadata_value({"SomeOtherKey": "value"})
        assert "SomeOtherKey" in result


class TestTrimMetadata:
    def test_under_limit_unchanged(self):
        meta = {"Modified": "2024-01-01", "Title": "short"}
        assert _trim_metadata(meta, "item1") == meta

    def test_over_limit_drops_longest(self):
        # 9 (key) + 8000 (val) + existing fixed keys ≈ 8034 bytes total → over limit
        long_val = "x" * 8000
        meta = {"Modified": "2024-01-01", "LongField": long_val, "Short": "abc"}
        result = _trim_metadata(meta, "item1")
        assert "Modified" in result
        assert "Short" in result
        assert "LongField" not in result

    def test_modified_always_kept(self):
        long_val = "x" * 8000
        meta = {"Modified": "2024-01-01", "BigField": long_val}
        result = _trim_metadata(meta, "item1")
        assert "Modified" in result
        assert "BigField" not in result

    def test_empty_metadata_unchanged(self):
        assert _trim_metadata({}, "item1") == {}


def _make_item(item_id, name, modified):
    return {"id": item_id, "name": name, "file": {}, "lastModifiedDateTime": modified}


class TestUploadChangedFiles:
    """_upload_changed_files reports the earliest modified_at among files it
    actually uploaded, so the caller can advance last-sync only that far
    instead of to now_utc — files left over when max_files is hit are
    retried on the next run instead of being permanently skipped (ISSUE-3),
    and the max_files cap is no longer fixed at 5 (ISSUE-2)."""

    def _patch_common(self, items, content_side_effect, fields=None):
        return [
            patch("function_app._get_drive_list_id", return_value="list-1"),
            patch("function_app._list_all_items", return_value=items),
            patch("function_app._fetch_item_fields", return_value=fields or {}),
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


class TestSanitizeMetadataKey:
    def test_plain_name_unchanged(self):
        assert _sanitize_metadata_key("HODSContentType") == "HODSContentType"

    def test_spaces_become_underscores(self):
        assert _sanitize_metadata_key("My Column") == "My_Column"

    def test_special_chars_become_underscores(self):
        assert _sanitize_metadata_key("Column-Name!") == "Column_Name_"

    def test_leading_digit_gets_prefix(self):
        assert _sanitize_metadata_key("1stColumn") == "_1stColumn"

    def test_empty_string_returns_fallback(self):
        assert _sanitize_metadata_key("") == "_field"


class TestIsSystemField:
    def test_at_prefix_is_system(self):
        assert _is_system_field("@odata.etag") is True

    def test_underscore_prefix_is_system(self):
        assert _is_system_field("_UIVersionString") is True

    def test_known_system_name_is_system(self):
        assert _is_system_field("FileRef") is True
        assert _is_system_field("ContentTypeId") is True

    def test_user_defined_field_not_system(self):
        assert _is_system_field("HODSContentType") is False
        assert _is_system_field("Prefix") is False
        assert _is_system_field("MyCustomColumn") is False


class TestDynamicMetadataInUpload:
    """Verifies that _upload_changed_files writes all non-system fields from
    the fields dict as blob metadata, sanitising keys, and skips system fields."""

    def test_non_system_fields_written_as_metadata(self):
        fields = {
            "HODSContentType": "Report",
            "PrefixLookupValue": "ABC",
            "_UIVersionString": "512",  # system — must be skipped
            "@odata.etag": "etag123",   # system — must be skipped
            "FileRef": "/sites/x",      # system — must be skipped
            "My Column": "value",       # space in name — must be sanitised
        }
        item = _make_item("1", "report.pdf", "2024-06-01T12:00:00Z")
        patches = [
            patch("function_app._get_drive_list_id", return_value="list-1"),
            patch("function_app._list_all_items", return_value=[item]),
            patch("function_app._fetch_item_fields", return_value=fields),
            patch("function_app.requests.get", return_value=_make_streamed_response()),
        ]
        for p in patches:
            p.start()
        try:
            blob_service_client = MagicMock()
            _upload_changed_files(
                blob_service_client=blob_service_client,
                container_name="ingest-output",
                drive_id="drive-1",
                site_id="site-1",
                last_sync=datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc),
                headers={},
            )
            call_kwargs = blob_service_client.get_blob_client.return_value.upload_blob.call_args[1]
            written = call_kwargs["metadata"]
            assert "Modified" in written
            assert "HODSContentType" in written
            assert "PrefixLookupValue" in written
            assert "My_Column" in written
            assert "_UIVersionString" not in written
            assert "@odata.etag" not in written  # noqa: S105
            assert "FileRef" not in written
        finally:
            for p in patches:
                p.stop()

    def test_metadata_columns_filter_limits_output(self):
        fields = {
            "HODSContentType": "Report",
            "PrefixLookupValue": "ABC",
            "AnotherColumn": "extra",
        }
        item = _make_item("1", "report.pdf", "2024-06-01T12:00:00Z")
        patches = [
            patch("function_app._get_drive_list_id", return_value="list-1"),
            patch("function_app._list_all_items", return_value=[item]),
            patch("function_app._fetch_item_fields", return_value=fields),
            patch("function_app.requests.get", return_value=_make_streamed_response()),
        ]
        for p in patches:
            p.start()
        try:
            blob_service_client = MagicMock()
            _upload_changed_files(
                blob_service_client=blob_service_client,
                container_name="ingest-output",
                drive_id="drive-1",
                site_id="site-1",
                last_sync=datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc),
                headers={},
                metadata_columns=frozenset({"HODSContentType"}),
            )
            call_kwargs = blob_service_client.get_blob_client.return_value.upload_blob.call_args[1]
            written = call_kwargs["metadata"]
            assert "HODSContentType" in written
            assert "Modified" in written          # always written
            assert "PrefixLookupValue" not in written
            assert "AnotherColumn" not in written
        finally:
            for p in patches:
                p.stop()

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


class TestPrefixLookupFallback:
    """Verifies HODS-specific Prefix resolution and HODSContentType warning."""

    def _run_upload(self, fields, extra_patches=()):
        item = _make_item("1", "report.pdf", "2024-06-01T12:00:00Z")
        base_patches = [
            patch("function_app._get_drive_list_id", return_value="list-1"),
            patch("function_app._list_all_items", return_value=[item]),
            patch("function_app._fetch_item_fields", return_value=fields),
            patch("function_app.requests.get", return_value=_make_streamed_response()),
        ]
        all_patches = base_patches + list(extra_patches)
        for p in all_patches:
            p.start()
        try:
            blob_service_client = MagicMock()
            _upload_changed_files(
                blob_service_client=blob_service_client,
                container_name="ingest-output",
                drive_id="drive-1",
                site_id="site-1",
                last_sync=datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc),
                headers={},
            )
            return blob_service_client.get_blob_client.return_value.upload_blob.call_args[1]["metadata"]
        finally:
            for p in all_patches:
                p.stop()

    def test_prefix_display_value_used_when_present(self):
        fields = {"HODSContentType": "Report", "PrefixLookupValue": "ALPHA"}
        with patch("function_app._get_lookup_column_info") as mock_info:
            written = self._run_upload(fields)
        assert written.get("Prefix") == "ALPHA"
        mock_info.assert_not_called()

    def test_prefix_resolved_via_lookup_when_id_only(self):
        fields = {"HODSContentType": "Report", "PrefixLookupId": "42"}
        extra = [
            patch(
                "function_app._get_lookup_column_info",
                return_value={"lookup_list_id": "lookup-list-1", "lookup_column": "Title"},
            ),
            patch("function_app._get_lookup_item_display_value", return_value="BETA"),
        ]
        written = self._run_upload(fields, extra_patches=extra)
        assert written.get("Prefix") == "BETA"

    def test_hods_content_type_missing_logs_warning(self):
        fields = {"PrefixLookupValue": "GAMMA"}
        with patch("function_app.logging.warning") as mock_warn:
            written = self._run_upload(fields)
        warned_calls = [str(c) for c in mock_warn.call_args_list]
        assert any("HODSContentType" in c for c in warned_calls)
        assert written.get("Prefix") == "GAMMA"


class TestToUtcIso:
    def test_utc_datetime(self):
        dt = datetime.datetime(2024, 6, 1, 12, 30, 0, tzinfo=datetime.timezone.utc)
        assert _to_utc_iso(dt) == "2024-06-01T12:30:00Z"

    def test_naive_datetime_treated_as_utc(self):
        dt = datetime.datetime(2024, 6, 1, 12, 30, 0)
        result = _to_utc_iso(dt)
        assert result.endswith("Z")
        assert "2024-06-01" in result


class TestGetAllowedExtensions:
    def test_default_is_pdf(self):
        with patch("function_app.os.getenv", return_value=None) as mock_env:
            mock_env.side_effect = lambda k, d=None: ".pdf" if k == "INGEST_FILE_EXTENSIONS" else d
            result = _get_allowed_extensions()
        assert result == [".pdf"]

    def test_star_star_returns_empty(self):
        with patch("function_app.os.getenv", return_value="**"):
            result = _get_allowed_extensions()
        assert result == []

    def test_semicolon_separated(self):
        with patch("function_app.os.getenv", return_value=".pdf;.docx"):
            result = _get_allowed_extensions()
        assert ".pdf" in result
        assert ".docx" in result

    def test_missing_dot_added(self):
        with patch("function_app.os.getenv", return_value="pdf"):
            result = _get_allowed_extensions()
        assert ".pdf" in result


class TestIsAllowedFileName:
    def test_matching_extension(self):
        assert _is_allowed_file_name("report.pdf", [".pdf"]) is True

    def test_non_matching_extension(self):
        assert _is_allowed_file_name("report.docx", [".pdf"]) is False

    def test_empty_list_allows_all(self):
        assert _is_allowed_file_name("report.docx", []) is True

    def test_case_insensitive(self):
        assert _is_allowed_file_name("REPORT.PDF", [".pdf"]) is True


class TestReadDeltaState:
    def test_returns_link_when_blob_exists(self):
        blob_svc = MagicMock()
        blob_svc.get_blob_client.return_value.download_blob.return_value.readall.return_value = (
            json.dumps({"deltaLink": "https://graph.microsoft.com/v1.0/drives/x/root/delta?token=abc"}).encode()
        )
        result = _read_delta_state(blob_svc, "container", "delta-state.json")
        assert result == "https://graph.microsoft.com/v1.0/drives/x/root/delta?token=abc"

    def test_returns_none_when_blob_missing(self):
        blob_svc = MagicMock()
        blob_svc.get_blob_client.return_value.download_blob.side_effect = Exception("not found")
        result = _read_delta_state(blob_svc, "container", "delta-state.json")
        assert result is None


class TestSaveDeltaState:
    def test_uploads_json_with_delta_link(self):
        blob_svc = MagicMock()
        _save_delta_state(
            blob_svc,
            "container",
            "https://example.com/delta?token=x",
            "delta-state.json",
            "/sites/HODS",
            "MyDrive",
        )
        upload_call = blob_svc.get_blob_client.return_value.upload_blob
        upload_call.assert_called_once()
        payload = json.loads(upload_call.call_args[0][0])
        assert payload["deltaLink"] == "https://example.com/delta?token=x"
        assert payload["driveName"] == "MyDrive"
        assert payload["sitePath"] == "/sites/HODS"
        assert "updatedAt" in payload


class TestGetDeltaChanges:
    def test_single_page_returns_items_and_link(self):
        final_link = "https://graph.microsoft.com/v1.0/drives/x/root/delta?token=new"
        with patch("function_app._graph_get", return_value={
            "value": [{"id": "1"}, {"id": "2"}],
            "@odata.deltaLink": final_link,
        }):
            items, link = _get_delta_changes("https://start-link", {}, max_items=100)
        assert len(items) == 2
        assert link == final_link

    def test_multi_page_collects_all_items(self):
        next_link = "https://page2"
        final_link = "https://final"
        pages = [
            {"value": [{"id": "1"}], "@odata.nextLink": next_link},
            {"value": [{"id": "2"}], "@odata.deltaLink": final_link},
        ]
        page_iter = iter(pages)
        with patch("function_app._graph_get", side_effect=lambda url, h: next(page_iter)):
            items, link = _get_delta_changes("https://start-link", {}, max_items=100)
        assert len(items) == 2
        assert link == final_link

    def test_raises_when_no_delta_link(self):
        with patch("function_app._graph_get", return_value={"value": []}):
            with pytest.raises(RuntimeError, match="deltaLink"):
                _get_delta_changes("https://start-link", {}, max_items=100)

    def test_max_items_truncates(self):
        final_link = "https://final"
        with patch("function_app._graph_get", return_value={
            "value": [{"id": str(i)} for i in range(10)],
            "@odata.deltaLink": final_link,
        }):
            items, _ = _get_delta_changes("https://start-link", {}, max_items=3)
        assert len(items) == 3


class TestUploadDriveItems:
    def _make_drive_item(self, item_id, name, modified="2024-06-01T12:00:00Z"):
        return {"id": item_id, "name": name, "file": {}, "lastModifiedDateTime": modified}

    def _run(self, items, allowed_extensions=None, extra_patches=()):
        if allowed_extensions is None:
            allowed_extensions = []
        base_patches = [
            patch("function_app._get_lookup_column_info", return_value=None),
            patch("function_app._fetch_item_fields", return_value={}),
            patch("function_app.requests.get", return_value=_make_streamed_response()),
            patch("function_app._extract_purpose_and_scope", return_value=None),
        ]
        all_patches = base_patches + list(extra_patches)
        for p in all_patches:
            p.start()
        try:
            blob_svc = MagicMock()
            count = _upload_drive_items(
                blob_svc, "container", "drive-1", "site-1",
                items, {}, "list-1", allowed_extensions,
            )
            return count, blob_svc
        finally:
            for p in all_patches:
                p.stop()

    def test_deleted_item_skipped(self):
        items = [{"id": "1", "name": "doc.pdf", "file": {}, "deleted": {}, "lastModifiedDateTime": "2024-06-01T00:00:00Z"}]
        count, _ = self._run(items)
        assert count == 0

    def test_non_file_skipped(self):
        items = [{"id": "1", "name": "folder", "lastModifiedDateTime": "2024-06-01T00:00:00Z"}]
        count, _ = self._run(items)
        assert count == 0

    def test_extension_filtered_skipped(self):
        items = [self._make_drive_item("1", "doc.docx")]
        count, _ = self._run(items, allowed_extensions=[".pdf"])
        assert count == 0

    def test_happy_path_uploads_file(self):
        items = [self._make_drive_item("1", "report.pdf")]
        count, _ = self._run(items, allowed_extensions=[".pdf"])
        assert count == 1

    def test_prefix_lookup_resolved_once(self):
        items = [self._make_drive_item("1", "a.pdf"), self._make_drive_item("2", "b.pdf")]
        extra = [patch("function_app._get_lookup_column_info", return_value={"lookup_list_id": "x", "lookup_column": "Title"})]
        with patch("function_app._fetch_item_fields", return_value={"PrefixLookupId": "5"}):
            with patch("function_app._get_lookup_item_display_value", return_value="ALPHA") as mock_resolve:
                with patch("function_app.requests.get", return_value=_make_streamed_response()):
                    with patch("function_app._extract_purpose_and_scope", return_value=None):
                        for p in extra:
                            p.start()
                        try:
                            blob_svc = MagicMock()
                            _upload_drive_items(blob_svc, "container", "drive-1", "site-1", items, {}, "list-1", [])
                        finally:
                            for p in extra:
                                p.stop()
        # validate by checking mock_resolve was called twice (once per item)
        assert mock_resolve.call_count == 2

    def test_purpose_and_scope_added_to_pdf_metadata(self):
        items = [self._make_drive_item("1", "report.pdf")]
        extracted_text = "This document defines the scope of the HODS project."
        extra = [patch("function_app._extract_purpose_and_scope", return_value=extracted_text)]
        count, blob_svc = self._run(items, extra_patches=extra)
        assert count == 1
        call_kwargs = blob_svc.get_blob_client.return_value.upload_blob.call_args[1]
        assert call_kwargs["metadata"].get("Purpose_and_Scope") == extracted_text

    def test_non_pdf_skips_extraction(self):
        items = [self._make_drive_item("1", "report.docx")]
        with patch("function_app._extract_purpose_and_scope") as mock_extract:
            with patch("function_app._get_lookup_column_info", return_value=None):
                with patch("function_app._fetch_item_fields", return_value={}):
                    with patch("function_app.requests.get", return_value=_make_streamed_response()):
                        blob_svc = MagicMock()
                        _upload_drive_items(blob_svc, "container", "drive-1", "site-1", items, {}, "list-1", [])
        mock_extract.assert_not_called()


class TestBuildBlobMetadata:
    def _run(self, fields, prefix_lookup_info=None, extra_patches=()):
        base_patches = [
            patch("function_app._fetch_item_fields", return_value=fields),
            patch("function_app._get_lookup_item_display_value", return_value=None),
        ]
        all_patches = base_patches + list(extra_patches)
        for p in all_patches:
            p.start()
        try:
            return _build_blob_metadata(
                "drive-1", "item-1", "site-1", "list-1",
                "2024-06-01T12:00:00Z", prefix_lookup_info, {},
            )
        finally:
            for p in all_patches:
                p.stop()

    def test_modified_always_set(self):
        result = self._run({})
        assert result.get("Modified") == "2024-06-01T12:00:00Z"

    def test_all_sp_fields_written(self):
        fields = {"CustomCol": "value1", "AnotherCol": "value2"}
        result = self._run(fields)
        assert "CustomCol" in result
        assert "AnotherCol" in result

    def test_system_fields_skipped(self):
        fields = {
            "@odata.etag": "etag",
            "_UIVersionString": "512",
            "FileRef": "/sites/x",
            "CustomCol": "keep",
        }
        result = self._run(fields)
        assert "@odata.etag" not in result  # noqa: S105
        assert "_UIVersionString" not in result
        assert "FileRef" not in result
        assert "CustomCol" in result

    def test_prefix_direct_value(self):
        fields = {"PrefixLookupValue": "ALPHA", "HODSContentType": "Report"}
        with patch("function_app._get_lookup_item_display_value") as mock_resolve:
            result = self._run(fields)
        assert result.get("Prefix") == "ALPHA"
        mock_resolve.assert_not_called()

    def test_prefix_resolved_via_lookup(self):
        fields = {"PrefixLookupId": "42", "HODSContentType": "Report"}
        prefix_info = {"lookup_list_id": "lookup-list-1", "lookup_column": "Title"}
        extra = [patch("function_app._get_lookup_item_display_value", return_value="BETA")]
        result = self._run(fields, prefix_lookup_info=prefix_info, extra_patches=extra)
        assert result.get("Prefix") == "BETA"

    def test_hods_content_type_stored_as_content_type_key(self):
        fields = {"HODSContentType": "Report", "PrefixLookupValue": "X"}
        result = self._run(fields)
        assert result.get("ContentType") == "Report"
        assert "HODSContentType" not in result

    def test_hods_content_type_missing_logs_warning(self):
        fields = {"PrefixLookupValue": "X"}
        with patch("function_app.logging.warning") as mock_warn:
            result = self._run(fields)
        warned_calls = [str(c) for c in mock_warn.call_args_list]
        assert any("HODSContentType" in c for c in warned_calls)
        assert "ContentType" not in result


class TestFetchContent:
    def test_returns_bytes_from_response(self):
        response = _make_streamed_response(chunks=[b"hello", b" world"])
        with patch("function_app.requests.get", return_value=response):
            result = _fetch_content("http://example/file.pdf", {})
        assert result == b"hello world"

    def test_raises_on_http_error(self):
        response = _make_streamed_response()
        response.raise_for_status.side_effect = requests.exceptions.HTTPError("403")
        with patch("function_app.requests.get", return_value=response):
            with pytest.raises(requests.exceptions.HTTPError):
                _fetch_content("http://example/file.pdf", {})


def _make_pdf_reader_mock(page_texts, title=None):
    """Build a minimal pypdf.PdfReader mock."""
    pages = []
    for text in page_texts:
        page = MagicMock()
        page.extract_text.return_value = text
        pages.append(page)
    reader = MagicMock()
    reader.pages = pages
    meta = {}
    if title is not None:
        meta["/Title"] = title
    reader.metadata = meta
    return reader


class TestExtractPurposeAndScope:
    def test_purpose_and_scope_heading_found(self):
        reader = _make_pdf_reader_mock([
            "1. Introduction\nSome intro.\n\n2. Purpose and Scope\nThis document defines the project scope.\n\n3. Background\nMore text."
        ])
        with patch("function_app.pypdf.PdfReader", return_value=reader):
            result = _extract_purpose_and_scope(b"fake-pdf")
        assert result is not None
        assert "defines the project scope" in result

    def test_purpose_only_heading_matched(self):
        reader = _make_pdf_reader_mock([
            "1. Purpose\nDefines the purpose of this standard.\n\n2. Next Section\nContent."
        ])
        with patch("function_app.pypdf.PdfReader", return_value=reader):
            result = _extract_purpose_and_scope(b"fake-pdf")
        assert result is not None
        assert "Defines the purpose" in result

    def test_scope_only_heading_matched(self):
        reader = _make_pdf_reader_mock([
            "Scope\nApplies to all HODS documents.\n\nBackground\nContent."
        ])
        with patch("function_app.pypdf.PdfReader", return_value=reader):
            result = _extract_purpose_and_scope(b"fake-pdf")
        assert result is not None
        assert "Applies to all HODS" in result

    def test_section_missing_falls_back_to_title(self):
        reader = _make_pdf_reader_mock(["Unrelated content only."], title="HODS Guidelines v2")
        with patch("function_app.pypdf.PdfReader", return_value=reader):
            result = _extract_purpose_and_scope(b"fake-pdf")
        assert result == "HODS Guidelines v2"

    def test_section_missing_no_title_returns_none(self):
        reader = _make_pdf_reader_mock(["Unrelated content."])
        with patch("function_app.pypdf.PdfReader", return_value=reader):
            result = _extract_purpose_and_scope(b"fake-pdf")
        assert result is None

    def test_pypdf_exception_returns_none(self):
        with patch("function_app.pypdf.PdfReader", side_effect=Exception("corrupt PDF")):
            result = _extract_purpose_and_scope(b"bad-bytes")
        assert result is None

    def test_searches_across_multiple_pages(self):
        reader = _make_pdf_reader_mock([
            "Cover page content.",
            "2. Purpose and Scope\nDefined across page 2.\n\n3. Background",
        ])
        with patch("function_app.pypdf.PdfReader", return_value=reader):
            result = _extract_purpose_and_scope(b"fake-pdf")
        assert result is not None
        assert "Defined across page 2" in result


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
