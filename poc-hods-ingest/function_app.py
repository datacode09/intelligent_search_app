import azure.functions as func
import datetime
import io
import json
import logging
import os
import re
import time
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode

import pypdf
import requests

from azure.storage.blob import BlobServiceClient

app = func.FunctionApp()

DELTA_STATE_BLOB_NAME_DEFAULT = "hods-library-delta-state.json"
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


def _retry(call, attempts=3, base_delay=1.0, retry_on=(requests.exceptions.RequestException,)):
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except retry_on as exc:
            if attempt == attempts:
                raise
            logging.warning("Retrying after error (attempt %s/%s): %s", attempt, attempts, exc)
            time.sleep(base_delay * (2 ** (attempt - 1)))


def _download_and_upload(content_url, headers, blob_client, metadata):
    with requests.get(content_url, headers=headers, stream=True, timeout=120) as content_response:
        content_response.raise_for_status()
        blob_client.upload_blob(
            content_response.iter_content(chunk_size=4 * 1024 * 1024),
            overwrite=True,
            metadata=metadata or None,
        )


_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


def _parse_last_sync(last_sync_raw: Optional[str], default: Optional[datetime.datetime] = None) -> datetime.datetime:
    if default is None:
        default = _EPOCH

    if not last_sync_raw:
        return default

    raw_value = last_sync_raw.strip()
    if not raw_value:
        return default

    # Support the previous format and ISO-8601 values for backward compatibility.
    formats = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"]
    for fmt in formats:
        try:
            parsed = datetime.datetime.strptime(raw_value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed.astimezone(datetime.timezone.utc)
        except ValueError:
            continue

    try:
        normalized = raw_value.replace("Z", "+00:00")
        parsed = datetime.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.astimezone(datetime.timezone.utc)
    except ValueError:
        logging.warning("Unrecognized last-sync format '%s'; defaulting to %s", raw_value, default.isoformat())
        return default


def _get_graph_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    token_response = requests.post(
        token_url,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    logging.info(f"Token response status: {token_response.status_code}")
    if token_response.status_code != 200:
        logging.error(f"Token request failed: {token_response.text}")
    token_response.raise_for_status()
    token_json = token_response.json()
    access_token = token_json.get("access_token")
    if not access_token:
        raise RuntimeError("Graph token response did not include access_token")
    logging.info("Successfully obtained Graph access token")
    return access_token


def _graph_get(url: str, headers: Dict[str, str], timeout: int = 60) -> Dict:
    response = requests.get(url, headers=headers, timeout=timeout)
    if not response.ok:
        logging.error("Graph GET %s -> %s Body=%s", url, response.status_code, response.text)
    response.raise_for_status()
    return response.json()


def _graph_get_with_params(url: str, headers: Dict[str, str], params: Dict) -> Dict:
    full_url = f"{url}?{urlencode(params)}"
    return _graph_get(full_url, headers)


def _get_site_id(hostname: str, site_path: str, headers: Dict[str, str]) -> str:
    site_url = f"{GRAPH_BASE_URL}/sites/{hostname}:{site_path}"
    site_json = _graph_get(site_url, headers)
    site_id = site_json.get("id")
    if not site_id:
        raise RuntimeError("Unable to resolve SharePoint site id")
    return site_id


def _resolve_site_id(hostname: str, site_path: str, headers: Dict[str, str], site_id_override: Optional[str] = None) -> str:
    if site_id_override:
        logging.info("Using configured SharePoint site id override: %s", site_id_override)
        return site_id_override
    return _get_site_id(hostname, site_path, headers)


def _normalize_drive_name(drive_name: str) -> str:
    return re.sub(r"\s+", " ", (drive_name or "").strip()).lower()


def _get_drive_id(site_id: str, drive_name: str, headers: Dict[str, str]) -> str:
    next_link: Optional[str] = f"{GRAPH_BASE_URL}/sites/{site_id}/drives?$top=200"
    requested_drive_name = _normalize_drive_name(drive_name)
    candidate_drive: Optional[Dict] = None
    while next_link:
        drives_response = _graph_get(next_link, headers)
        for drive in drives_response.get("value", []):
            current_drive_name = _normalize_drive_name(drive.get("name", ""))
            if current_drive_name == requested_drive_name:
                drive_id = drive.get("id")
                if drive_id:
                    return drive_id

            if candidate_drive is None and drive.get("id"):
                candidate_drive = drive
        next_link = drives_response.get("@odata.nextLink")

    if candidate_drive and candidate_drive.get("id"):
        return candidate_drive["id"]

    raise RuntimeError(f"Drive '{drive_name}' not found in site '{site_id}'")


def _list_all_items(drive_id: str, headers: Dict[str, str]) -> Iterable[Dict]:
    # Breadth-first listing of all items in a drive to support nested folders.
    queue: List[str] = ["root"]
    while queue:
        parent = queue.pop(0)
        next_link: Optional[str] = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{parent}/children?$top=200"
        while next_link:
            children_page = _graph_get(next_link, headers)
            for item in children_page.get("value", []):
                yield item
                if "folder" in item:
                    child_id = item.get("id")
                    if child_id:
                        queue.append(child_id)
            next_link = children_page.get("@odata.nextLink")


def _to_blob_name(file_name: str) -> str:
    base_name = os.path.basename((file_name or "").strip())
    normalized = re.sub(r"[^0-9A-Za-z._-]+", "_", base_name)
    normalized = re.sub(r"_+", "_", normalized).strip("._-")
    if not normalized:
        normalized = "file"
    return normalized


_SYSTEM_FIELDS = frozenset({
    "id", "ID", "ContentTypeId", "FileRef", "FileDirRef", "FileLeafRef",
    "FSObjType", "UniqueId", "owshiddenversion", "ProgId", "ScopeId",
    "InstanceID", "Order", "GUID", "WorkflowVersion", "WorkflowInstanceID",
    "ParentVersionString", "ParentLeafName",
})


def _is_system_field(key: str) -> bool:
    """Return True for SharePoint internal/system fields that should not become blob metadata."""
    if key.startswith(("@", "_")):
        return True
    return key in _SYSTEM_FIELDS


def _sanitize_metadata_key(key: str) -> str:
    """Convert a SharePoint field name to a valid Azure Blob metadata key.

    Blob metadata keys must be valid C# identifiers: [a-zA-Z_][a-zA-Z0-9_]*.
    """
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", key)
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized or "_field"


def _to_blob_metadata_value(raw_value: object) -> str:
    if raw_value is None:
        return ""
    if isinstance(raw_value, list):
        # Multi-value lookup columns: list of {"LookupId": ..., "LookupValue": ...}
        # or managed metadata: list of {"Label": ..., "TermGuid": ...}.
        values: List[str] = []
        for item in raw_value:
            if isinstance(item, dict):
                display = (
                    item.get("LookupValue") or item.get("lookupValue") or item.get("Label")
                )
                values.append(str(display) if display is not None else str(item))
            else:
                values.append(str(item))
        text = json.dumps(values, ensure_ascii=True)
    elif isinstance(raw_value, dict):
        # Single-value lookup: {"LookupId": ..., "LookupValue": ...}
        # Managed metadata (taxonomy): {"Label": "...", "TermGuid": "...", "WssId": ...}
        display = (
            raw_value.get("LookupValue") or raw_value.get("lookupValue") or raw_value.get("Label")
        )
        text = str(display) if display is not None else str(raw_value)
    else:
        text = str(raw_value)
    # Azure Blob metadata values are ASCII-only; drop unsupported chars.
    return text.encode("ascii", errors="ignore").decode("ascii")


_BLOB_METADATA_MAX_BYTES = 8000  # 8 KB limit minus HTTP header overhead


def _trim_metadata(metadata: Dict[str, str], item_id: str = "") -> Dict[str, str]:
    """Drop values (longest first) until total byte count fits within 8 KB.

    Azure Blob Storage rejects upload_blob calls whose combined metadata
    key+value bytes exceed 8,192. 'Modified' is always preserved.
    """
    total = sum(len(k) + len(v) for k, v in metadata.items())
    if total <= _BLOB_METADATA_MAX_BYTES:
        return metadata
    protected = {k: v for k, v in metadata.items() if k == "Modified"}
    candidates = sorted(
        [(k, v) for k, v in metadata.items() if k != "Modified"],
        key=lambda kv: len(kv[1]),
        reverse=True,
    )
    result = dict(protected)
    for k, v in candidates:
        if sum(len(kk) + len(vv) for kk, vv in result.items()) + len(k) + len(v) <= _BLOB_METADATA_MAX_BYTES:
            result[k] = v
        else:
            logging.warning("Metadata key '%s' dropped for item %s: would exceed 8 KB limit", k, item_id)
    return result


def _get_drive_list_id(drive_id: str, headers: Dict[str, str]) -> str:
    """Return the SharePoint list GUID for the document library behind a drive."""
    url = f"{GRAPH_BASE_URL}/drives/{drive_id}/list"
    list_json = _graph_get(url, headers)
    list_id = list_json.get("id")
    if not list_id:
        raise RuntimeError(f"Could not resolve SharePoint list ID for drive '{drive_id}'")
    return list_id


def _to_utc_iso(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_allowed_extensions() -> List[str]:
    raw = os.getenv("INGEST_FILE_EXTENSIONS", ".pdf").strip()
    if raw == "**":
        return []
    return [
        (e.strip() if e.strip().startswith(".") else "." + e.strip()).lower()
        for e in raw.split(";") if e.strip()
    ]


def _is_allowed_file_name(name: str, exts: List[str]) -> bool:
    if not exts:
        return True
    _, ext = os.path.splitext((name or "").lower())
    return ext in exts


def _ensure_container(blob_svc: BlobServiceClient, container: str) -> None:
    try:
        blob_svc.get_container_client(container).create_container()
        logging.info("Created blob container '%s'", container)
    except Exception:
        pass  # already exists


def _read_delta_state(blob_svc: BlobServiceClient, container: str, blob_name: str) -> Optional[str]:
    try:
        data = blob_svc.get_blob_client(container=container, blob=blob_name).download_blob().readall()
        return json.loads(data).get("deltaLink")
    except Exception:
        return None


def _save_delta_state(
    blob_svc: BlobServiceClient,
    container_name: str,
    delta_link: str,
    state_blob_name: str,
    site_path: str,
    drive_name: str,
) -> None:
    state = {
        "deltaLink": delta_link,
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "sitePath": site_path,
        "driveName": drive_name,
    }
    blob_svc.get_blob_client(container=container_name, blob=state_blob_name).upload_blob(
        json.dumps(state), overwrite=True
    )


def _initialize_delta_tracking_latest(drive_id: str, headers: Dict[str, str]) -> str:
    url = f"{GRAPH_BASE_URL}/drives/{drive_id}/root/delta?token=latest"
    data = _graph_get(url, headers)
    delta_link = data.get("@odata.deltaLink")
    if not delta_link:
        raise RuntimeError("No @odata.deltaLink in delta?token=latest response")
    return delta_link


def _get_delta_changes(
    delta_link: str,
    headers: Dict[str, str],
    max_items: int,
) -> "Tuple[List[Dict], str]":
    changes: List[Dict] = []
    final_delta_link: Optional[str] = None
    url: Optional[str] = delta_link
    while url:
        data = _graph_get(url, headers)
        page_items = data.get("value", [])
        for item in page_items:
            if len(changes) >= max_items:
                logging.warning(
                    "Delta result hit max_items cap: %s. Continuing to final deltaLink, but not appending more.",
                    max_items,
                )
                break
            changes.append(item)
        new_delta_link = data.get("@odata.deltaLink")
        if new_delta_link:
            final_delta_link = new_delta_link
            url = None
        else:
            url = data.get("@odata.nextLink")
    if not final_delta_link:
        raise RuntimeError("Delta query completed without a final @odata.deltaLink")
    return changes[:max_items], final_delta_link


def _list_recent_files_from_hods_list(
    site_id: str,
    list_id: str,
    drive_id: str,
    drive_name: str,
    headers: Dict[str, str],
    cutoff: datetime.datetime,
    max_files: int,
    allowed_extensions: List[str],
) -> List[Dict]:
    cutoff_text = _to_utc_iso(cutoff)
    params = {
        "$expand": "fields,driveItem($select=id,name,webUrl,lastModifiedDateTime,size,file,folder,parentReference)",
        "$filter": f"fields/Modified ge '{cutoff_text}'",
        "$top": "200",
    }
    base_url = f"{GRAPH_BASE_URL}/sites/{site_id}/lists/{list_id}/items"
    items: List[Dict] = []
    page = _graph_get_with_params(base_url, headers, params)
    while True:
        for list_item in page.get("value", []):
            drive_item = list_item.get("driveItem") or {}
            name = drive_item.get("name", "")
            if not _is_allowed_file_name(name, allowed_extensions):
                continue
            if "file" not in drive_item:
                continue
            items.append(drive_item)
            if len(items) >= max_files:
                return items[:max_files]
        next_url = page.get("@odata.nextLink")
        if not next_url:
            break
        page = _graph_get(next_url, headers)
    return items[:max_files]


def _build_blob_metadata(
    drive_id: str,
    item_id: str,
    site_id: str,
    list_id: str,
    modified_raw: Optional[str],
    prefix_lookup_info: Optional[Dict[str, str]],
    headers: Dict[str, str],
) -> Dict[str, str]:
    # Fields handled explicitly below — skip in the generic loop to avoid duplicates.
    _EXPLICIT_FIELDS = frozenset({"HODSContentType", "PrefixLookupValue", "PrefixLookupId"})

    metadata: Dict[str, str] = {}
    metadata["Modified"] = _to_blob_metadata_value(modified_raw)
    fields = _fetch_item_fields(drive_id, item_id, site_id, list_id, headers)
    # Write ALL non-system SharePoint fields as blob metadata.
    for key, value in fields.items():
        if _is_system_field(key):
            continue
        if key in _EXPLICIT_FIELDS:
            continue
        blob_key = _sanitize_metadata_key(key)
        blob_val = _to_blob_metadata_value(value)
        if blob_val:
            metadata[blob_key] = blob_val
    # Prefix: use display value if present, else resolve from lookup list.
    prefix_raw = fields.get("PrefixLookupValue")
    if prefix_raw is None:
        prefix_lookup_id = fields.get("PrefixLookupId")
        if prefix_lookup_id is not None and prefix_lookup_info is not None:
            prefix_raw = _get_lookup_item_display_value(
                site_id,
                prefix_lookup_info["lookup_list_id"],
                prefix_lookup_id,
                prefix_lookup_info["lookup_column"],
                headers,
            )
    if prefix_raw is not None:
        metadata["Prefix"] = _to_blob_metadata_value(prefix_raw)
    else:
        logging.warning("Could not resolve Prefix display value for item %s", item_id)
    # HODSContentType → stored as "ContentType" key.
    hods_content_type_raw = fields.get("HODSContentType")
    if hods_content_type_raw is not None:
        metadata["ContentType"] = _to_blob_metadata_value(hods_content_type_raw)
    else:
        logging.warning("'HODSContentType' field not found for item %s", item_id)
    return metadata


_PURPOSE_HEADING_RE = re.compile(
    r"(?im)^[ \t]*(?:\d[\d.]*\.?\s+)?(?:purpose(?:\s+(?:and|&)\s+scope)?|scope)[ \t]*$"
)
_NEXT_HEADING_RE = re.compile(
    r"(?im)^[ \t]*(?:\d[\d.]*\.?\s+[A-Za-z]|[A-Z][A-Z\s]{4,})[ \t]*$"
)


def _fetch_content(content_url: str, headers: Dict[str, str], timeout: int = 120) -> bytes:
    """Download file content into memory and return as bytes."""
    with requests.get(content_url, headers=headers, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        return b"".join(r.iter_content(chunk_size=4 * 1024 * 1024))


def _extract_purpose_and_scope(pdf_bytes: bytes) -> Optional[str]:
    """Return the body of the Purpose/Scope section from a PDF, or the document title as fallback."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        logging.warning("pypdf: could not read PDF for section extraction: %s", exc)
        return None

    max_pages = min(5, len(reader.pages))
    page_texts: List[str] = []
    for i in range(max_pages):
        try:
            page_texts.append(reader.pages[i].extract_text() or "")
        except Exception:
            page_texts.append("")
    full_text = "\n".join(page_texts)

    heading_match = _PURPOSE_HEADING_RE.search(full_text)
    if heading_match:
        body_start = heading_match.end()
        next_match = _NEXT_HEADING_RE.search(full_text, body_start)
        body = full_text[body_start: next_match.start() if next_match else len(full_text)]
        body = body.strip()
        if body:
            return body

    # Fallback: PDF document title from metadata
    try:
        info = reader.metadata
        if info:
            title = str(info.get("/Title") or info.get("Title") or "").strip()
            if title:
                return title
    except Exception:
        pass

    return None


def _upload_drive_items(
    blob_svc: BlobServiceClient,
    container: str,
    drive_id: str,
    site_id: str,
    items: List[Dict],
    headers: Dict[str, str],
    list_id: str,
    allowed_extensions: List[str],
) -> int:
    uploaded = 0
    prefix_lookup_info = _get_lookup_column_info(site_id, list_id, "Prefix", headers)
    for item in items:
        if "deleted" in item:
            continue
        if "file" not in item:
            continue
        name = item.get("name", "")
        if not _is_allowed_file_name(name, allowed_extensions):
            continue
        item_id = item.get("id")
        if not item_id:
            continue
        modified_raw = item.get("lastModifiedDateTime", "")
        content_url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/content"
        blob_name = _to_blob_name(name)
        blob_client = blob_svc.get_blob_client(container=container, blob=blob_name)

        metadata = _build_blob_metadata(
            drive_id, item_id, site_id, list_id, modified_raw, prefix_lookup_info, headers
        )

        # Native drive item properties not available inside _build_blob_metadata.
        _created_raw = item.get("createdDateTime")
        if _created_raw:
            metadata["Created"] = _to_blob_metadata_value(_created_raw)
        _size = item.get("size")
        if _size is not None:
            metadata["Size"] = str(_size)
        _created_by = ((item.get("createdBy") or {}).get("user") or {}).get("displayName")
        if _created_by:
            metadata["CreatedBy"] = _to_blob_metadata_value(_created_by)
        _modified_by = ((item.get("lastModifiedBy") or {}).get("user") or {}).get("displayName")
        if _modified_by:
            metadata["ModifiedBy"] = _to_blob_metadata_value(_modified_by)

        # Download to memory so PDF text can be extracted before we upload.
        raw_bytes = _retry(lambda: _fetch_content(content_url, headers))
        if name.lower().endswith(".pdf"):
            extracted = _extract_purpose_and_scope(raw_bytes)
            if extracted:
                metadata["Purpose_and_Scope"] = _to_blob_metadata_value(extracted)

        metadata = _trim_metadata(metadata, item_id)
        logging.info("Item %s metadata keys written: %s", item_id, sorted(metadata.keys()))
        _retry(lambda: blob_client.upload_blob(raw_bytes, overwrite=True, metadata=metadata or None))
        uploaded += 1
    return uploaded


def _get_lookup_column_info(site_id: str, list_id: str, column_name: str, headers: Dict[str, str]) -> Optional[Dict[str, str]]:
    """Return lookup metadata (target list + target column) for a list column."""
    columns_url = f"{GRAPH_BASE_URL}/sites/{site_id}/lists/{list_id}/columns?$select=name,displayName,lookup"
    columns_json = _graph_get(columns_url, headers)
    target = (column_name or "").strip().lower()

    for column in columns_json.get("value", []):
        name = (column.get("name") or "").strip().lower()
        display_name = (column.get("displayName") or "").strip().lower()
        if target not in {name, display_name}:
            continue

        lookup = column.get("lookup") or {}
        lookup_list_id = lookup.get("listId")
        if not lookup_list_id:
            return None

        # The lookup source column is often "Title" when the list displays Name.
        # Keep a robust fallback chain when reading the lookup item fields.
        lookup_column = lookup.get("columnName") or "Title"
        return {
            "lookup_list_id": str(lookup_list_id),
            "lookup_column": str(lookup_column),
        }

    return None


def _get_lookup_item_display_value(
    site_id: str,
    lookup_list_id: str,
    lookup_item_id: object,
    lookup_column: str,
    headers: Dict[str, str],
) -> Optional[str]:
    """Resolve a lookup item ID to human-readable text from the lookup list."""
    if lookup_item_id is None:
        return None

    lookup_item_id_text = str(lookup_item_id).strip()
    if not lookup_item_id_text:
        return None

    select = f"{lookup_column},Title,Name"
    lookup_url = (
        f"{GRAPH_BASE_URL}/sites/{site_id}/lists/{lookup_list_id}"
        f"/items/{lookup_item_id_text}?$expand=fields($select={select})"
    )

    try:
        lookup_json = _graph_get(lookup_url, headers)
    except Exception as exc:
        logging.warning("Failed to resolve lookup item %s from list %s: %s", lookup_item_id_text, lookup_list_id, exc)
        return None

    fields = (lookup_json.get("fields") or {})
    for candidate in [lookup_column, "Title", "Name"]:
        value = fields.get(candidate)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _fetch_item_fields(
    drive_id: str,
    item_id: str,
    site_id: str,
    list_id: str,
    headers: Dict[str, str],
) -> Dict:
    """Return the SharePoint list-item fields dict for a drive item.

    Uses the sites/lists endpoint which reliably returns lookup display values
    (LookupValue) that the drive-based endpoint sometimes omits.
    """
    # Step 1: resolve the SharePoint list item integer ID from the drive item.
    sp_id_url = (
        f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}"
        f"?$expand=listItem($select=id)"
    )
    sp_id_json = _graph_get(sp_id_url, headers)
    sp_item_id = ((sp_id_json.get("listItem") or {}).get("id"))
    if not sp_item_id:
        logging.warning("Could not resolve SharePoint item ID for drive item %s", item_id)
        return {}

    # Step 2: fetch all fields — no $select so every column defined on the
    # library is returned, including lookup display values that the drive-based
    # endpoint omits.
    fields_url = (
        f"{GRAPH_BASE_URL}/sites/{site_id}/lists/{list_id}"
        f"/items/{sp_item_id}?$expand=fields"
    )
    fields_json = _graph_get(fields_url, headers)
    fields = (fields_json.get("fields") or {})
    logging.info("Item %s available field keys: %s", item_id, sorted(fields.keys()))
    return fields


def _upload_changed_files(
    blob_service_client: BlobServiceClient,
    container_name: str,
    drive_id: str,
    site_id: str,
    last_sync: datetime.datetime,
    headers: Dict[str, str],
    max_files: int = 500,
    metadata_columns: Optional[frozenset] = None,
) -> "tuple[int, Optional[datetime.datetime]]":
    # Resolve the SharePoint list ID once for the whole run.
    list_id = _get_drive_list_id(drive_id, headers)
    uploaded = 0
    earliest_success: Optional[datetime.datetime] = None
    latest_success: Optional[datetime.datetime] = None
    cap_hit = False
    for item in _list_all_items(drive_id, headers):
        if uploaded >= max_files:
            cap_hit = True
            break

        if "file" not in item:
            continue

        modified_raw = item.get("lastModifiedDateTime")
        if not modified_raw:
            continue

        modified_at = _parse_last_sync(modified_raw)
        if modified_at <= last_sync:
            continue

        item_id = item.get("id")
        file_name = item.get("name")
        if not item_id or not file_name:
            continue

        content_url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/content"

        blob_name = _to_blob_name(file_name)
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)

        # Fetch all SharePoint list-item fields for this file.
        fields = _fetch_item_fields(drive_id, item_id, site_id, list_id, headers)

        metadata: Dict[str, str] = {}

        # Native drive item properties — available without an extra API call.
        metadata["Modified"] = _to_blob_metadata_value(modified_raw)
        _created_raw = item.get("createdDateTime")
        if _created_raw:
            metadata["Created"] = _to_blob_metadata_value(_created_raw)
        _size = item.get("size")
        if _size is not None:
            metadata["Size"] = str(_size)
        _created_by = ((item.get("createdBy") or {}).get("user") or {}).get("displayName")
        if _created_by:
            metadata["CreatedBy"] = _to_blob_metadata_value(_created_by)
        _modified_by = ((item.get("lastModifiedBy") or {}).get("user") or {}).get("displayName")
        if _modified_by:
            metadata["ModifiedBy"] = _to_blob_metadata_value(_modified_by)

        # Write every non-system SharePoint column as blob metadata.
        # INGEST_METADATA_COLUMNS optionally limits which columns are written.
        for key, value in fields.items():
            if _is_system_field(key):
                continue
            if metadata_columns is not None and key not in metadata_columns:
                continue
            blob_key = _sanitize_metadata_key(key)
            blob_val = _to_blob_metadata_value(value)
            if blob_val:
                metadata[blob_key] = blob_val

        # HODS-specific: resolve Prefix display value when Graph returns only the lookup ID.
        prefix_display = fields.get("PrefixLookupValue")
        if prefix_display is None:
            prefix_lookup_id = fields.get("PrefixLookupId")
            if prefix_lookup_id is not None:
                prefix_lookup_info = _get_lookup_column_info(site_id, list_id, "Prefix", headers)
                if prefix_lookup_info is not None:
                    prefix_display = _get_lookup_item_display_value(
                        site_id,
                        prefix_lookup_info["lookup_list_id"],
                        prefix_lookup_id,
                        prefix_lookup_info["lookup_column"],
                        headers,
                    )
        if prefix_display is not None:
            metadata["Prefix"] = _to_blob_metadata_value(prefix_display)

        # HODS-specific: warn if HODSContentType column is absent.
        if fields.get("HODSContentType") is None:
            logging.warning("'HODSContentType' field not found for item %s", item_id)

        metadata = _trim_metadata(metadata, item_id)
        logging.info("Item %s metadata keys written: %s", item_id, sorted(metadata.keys()))

        _retry(lambda: _download_and_upload(content_url, headers, blob_client, metadata))
        uploaded += 1
        if earliest_success is None or modified_at < earliest_success:
            earliest_success = modified_at
        if latest_success is None or modified_at > latest_success:
            latest_success = modified_at

    # If the per-run cap was hit, only advance last-sync to the earliest
    # uploaded file's time so files left over past the cap aren't skipped
    # on the next run. Otherwise every changed file was processed, so
    # advance to the latest uploaded file's time to avoid re-uploading the
    # same files again next run.
    sync_point = earliest_success if cap_hit else latest_success
    return uploaded, sync_point

# Schedule is configurable via INGEST_SCHEDULE_CRON (defaults to hourly) so it
# can be tightened for local testing without hitting MS Graph throttling
# (HTTP 429) at production cadence.
@app.timer_trigger(schedule=os.getenv("INGEST_SCHEDULE_CRON", "0 0 * * * *"), arg_name="myTimer", run_on_startup=False,
              use_monitor=False)
def Ingest(myTimer: func.TimerRequest) -> None:

    if myTimer.past_due:
        logging.info("The timer is past due!")

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    blob_connection_string = os.getenv("BLOB_STORAGE_CONNECTION_STRING")
    container_name = os.getenv("BLOB_CONTAINER_NAME", "ingest-output")
    tenant_id = os.getenv("SHAREPOINT_TENANT_ID")
    client_id = os.getenv("SHAREPOINT_CLIENT_ID")
    client_secret = os.getenv("SHAREPOINT_CLIENT_SECRET")
    site_hostname = os.getenv("SHAREPOINT_SITE_HOSTNAME")
    site_path = os.getenv("SHAREPOINT_SITE_PATH")
    site_id_override = os.getenv("SHAREPOINT_SITE_ID")
    drive_name = os.getenv("SHAREPOINT_LIBRARY_DRIVE_NAME", "Documents")
    delta_state_blob_name = os.getenv("DELTA_STATE_BLOB_NAME", DELTA_STATE_BLOB_NAME_DEFAULT)
    max_files = int(os.getenv("INGEST_MAX_FILES_PER_RUN", "100"))
    allowed_extensions = _get_allowed_extensions()

    if not blob_connection_string:
        logging.error("Missing app setting: BLOB_STORAGE_CONNECTION_STRING")
        return

    required_sharepoint_settings = {
        "SHAREPOINT_TENANT_ID": tenant_id,
        "SHAREPOINT_CLIENT_ID": client_id,
        "SHAREPOINT_CLIENT_SECRET": client_secret,
        "SHAREPOINT_SITE_HOSTNAME": site_hostname,
        "SHAREPOINT_SITE_PATH": site_path,
    }
    missing = [k for k, v in required_sharepoint_settings.items() if not v]
    if missing:
        logging.error("Missing SharePoint app settings: %s", ", ".join(missing))
        return

    # TODO [ISSUE-7 HIGH]: Uses a connection string (stored secret) instead of Managed Identity.
    # The Function App already has a System-Assigned Managed Identity (assigned in main.bicep)
    # and the Storage Blob Data Contributor role can be granted via Bicep.
    #
    # Fix — replace with:
    #   from azure.identity import DefaultAzureCredential
    #   storage_account_url = os.getenv("BLOB_STORAGE_ACCOUNT_URL")  # e.g. https://<account>.blob.core.windows.net
    #   blob_service_client = BlobServiceClient(storage_account_url, DefaultAzureCredential())
    #
    # In Bicep, add a Storage Blob Data Contributor role assignment:
    #   resource fnStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
    #     name: guid(storageAccount.id, functionApp.id, 'blob-contributor')
    #     scope: storageAccount
    #     properties: {
    #       roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    #       principalId: functionApp.identity.principalId
    #       principalType: 'ServicePrincipal'
    #     }
    #   }
    #
    # To test: remove BLOB_STORAGE_CONNECTION_STRING from local.settings.json, set
    # BLOB_STORAGE_ACCOUNT_URL, run `az login`, then `func start` — blobs should
    # upload using your local identity.
    blob_service_client = BlobServiceClient.from_connection_string(blob_connection_string)
    _ensure_container(blob_service_client, container_name)

    try:
        token = _get_graph_token(tenant_id, client_id, client_secret)
        headers = {"Authorization": f"Bearer {token}"}
        site_id = _resolve_site_id(site_hostname, site_path, headers, site_id_override)
        drive_id = _get_drive_id(site_id, drive_name, headers)
        list_id = _get_drive_list_id(drive_id, headers)

        logging.info("Resolved source: Site ID=%s Drive Name=%s Drive ID=%s List ID=%s", site_id, drive_name, drive_id, list_id)
        logging.info("Allowed extensions: %s", allowed_extensions)

        existing_delta_link = _read_delta_state(blob_service_client, container_name, delta_state_blob_name)

        if existing_delta_link is None:
            # First run: seed from recent SP list items, then capture a delta baseline.
            start_date_raw = os.getenv("INGEST_START_DATE")
            cutoff = _parse_last_sync(start_date_raw) if start_date_raw else now_utc
            logging.info("First run: seeding from SP list items modified since %s", _to_utc_iso(cutoff))
            recent_items = _list_recent_files_from_hods_list(
                site_id, list_id, drive_id, drive_name, headers, cutoff, max_files, allowed_extensions,
            )
            uploaded_count = _upload_drive_items(
                blob_service_client, container_name, drive_id, site_id,
                recent_items, headers, list_id, allowed_extensions,
            )
            new_delta_link = _initialize_delta_tracking_latest(drive_id, headers)
            _save_delta_state(blob_service_client, container_name, new_delta_link, delta_state_blob_name, site_path, drive_name)
            logging.info("First-run ingestion completed. Recent files uploaded: %s", uploaded_count)
        else:
            # Incremental run: fetch only changed items since last delta.
            logging.info("Incremental run: fetching delta changes")
            raw_delta_items, refreshed_delta_link = _get_delta_changes(existing_delta_link, headers, max_files)
            uploaded_count = _upload_drive_items(
                blob_service_client, container_name, drive_id, site_id,
                raw_delta_items, headers, list_id, allowed_extensions,
            )
            _save_delta_state(blob_service_client, container_name, refreshed_delta_link, delta_state_blob_name, site_path, drive_name)
            logging.info("Ingestion completed. Files uploaded: %s", uploaded_count)

    except Exception as exc:
        logging.exception("Failed to sync SharePoint files: %s", exc)


def _list_all_files_from_hods_list(
    site_id: str,
    list_id: str,
    headers: Dict[str, str],
    allowed_extensions: List[str],
    start_date: Optional[datetime.datetime] = None,
    end_date: Optional[datetime.datetime] = None,
    max_files: int = 5000,
) -> List[Dict]:
    """Return all drive items from a SharePoint list, with optional date-range filter.

    Unlike _list_recent_files_from_hods_list (which always requires a cutoff),
    this function defaults to the full library when no dates are given — intended
    for one-off historical loads.
    """
    params: Dict[str, str] = {
        "$expand": "fields,driveItem($select=id,name,webUrl,lastModifiedDateTime,size,file,folder,parentReference)",
        "$top": "200",
    }
    date_filters: List[str] = []
    if start_date:
        date_filters.append(f"fields/Modified ge '{_to_utc_iso(start_date)}'")
    if end_date:
        date_filters.append(f"fields/Modified le '{_to_utc_iso(end_date)}'")
    if date_filters:
        params["$filter"] = " and ".join(date_filters)

    base_url = f"{GRAPH_BASE_URL}/sites/{site_id}/lists/{list_id}/items"
    items: List[Dict] = []
    page = _graph_get_with_params(base_url, headers, params)
    while True:
        for list_item in page.get("value", []):
            drive_item = list_item.get("driveItem") or {}
            name = drive_item.get("name", "")
            if not _is_allowed_file_name(name, allowed_extensions):
                continue
            if "file" not in drive_item:
                continue
            items.append(drive_item)
            if len(items) >= max_files:
                return items[:max_files]
        next_url = page.get("@odata.nextLink")
        if not next_url:
            break
        page = _graph_get(next_url, headers)
    return items[:max_files]


def _parse_historical_date_param(value: Optional[str], name: str) -> Optional[datetime.datetime]:
    """Parse a date string from an HTTP query parameter; log a 400-level warning on bad input."""
    if not value:
        return None
    dt = _parse_last_sync(value)
    if dt == _EPOCH:
        logging.warning("Historical ingest: could not parse '%s' param '%s'; ignoring.", name, value)
        return None
    return dt


@app.route(route="IngestHistorical", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def IngestHistorical(req: func.HttpRequest) -> func.HttpResponse:
    """HTTP-triggered function for one-off historical data loads.

    Query parameters (all optional):
      start_date  ISO-8601 string — only ingest files modified on or after this date.
      end_date    ISO-8601 string — only ingest files modified on or before this date.
      max_files   Integer cap on files processed in this call (default: HISTORICAL_MAX_FILES
                  env var, or 5000).

    Returns a JSON body with { "uploaded": N, "start_date": ..., "end_date": ... }.
    Does NOT modify the delta-state blob used by the incremental Ingest timer function.
    """
    logging.info("IngestHistorical triggered")

    blob_connection_string = os.getenv("BLOB_STORAGE_CONNECTION_STRING")
    container_name = os.getenv("BLOB_CONTAINER_NAME", "ingest-output")
    tenant_id = os.getenv("SHAREPOINT_TENANT_ID")
    client_id = os.getenv("SHAREPOINT_CLIENT_ID")
    client_secret = os.getenv("SHAREPOINT_CLIENT_SECRET")
    site_hostname = os.getenv("SHAREPOINT_SITE_HOSTNAME")
    site_path = os.getenv("SHAREPOINT_SITE_PATH")
    site_id_override = os.getenv("SHAREPOINT_SITE_ID")
    drive_name = os.getenv("SHAREPOINT_LIBRARY_DRIVE_NAME", "Documents")
    allowed_extensions = _get_allowed_extensions()

    default_max = int(os.getenv("HISTORICAL_MAX_FILES", "5000"))
    try:
        max_files = int(req.params.get("max_files") or default_max)
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "max_files must be an integer"}),
            status_code=400,
            mimetype="application/json",
        )

    start_date = _parse_historical_date_param(req.params.get("start_date"), "start_date")
    end_date = _parse_historical_date_param(req.params.get("end_date"), "end_date")

    if not blob_connection_string:
        return func.HttpResponse(
            json.dumps({"error": "Missing app setting: BLOB_STORAGE_CONNECTION_STRING"}),
            status_code=500,
            mimetype="application/json",
        )

    required = {
        "SHAREPOINT_TENANT_ID": tenant_id,
        "SHAREPOINT_CLIENT_ID": client_id,
        "SHAREPOINT_CLIENT_SECRET": client_secret,
        "SHAREPOINT_SITE_HOSTNAME": site_hostname,
        "SHAREPOINT_SITE_PATH": site_path,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        return func.HttpResponse(
            json.dumps({"error": f"Missing app settings: {', '.join(missing)}"}),
            status_code=500,
            mimetype="application/json",
        )

    try:
        blob_service_client = BlobServiceClient.from_connection_string(blob_connection_string)
        _ensure_container(blob_service_client, container_name)

        token = _get_graph_token(tenant_id, client_id, client_secret)
        headers = {"Authorization": f"Bearer {token}"}
        site_id = _resolve_site_id(site_hostname, site_path, headers, site_id_override)
        drive_id = _get_drive_id(site_id, drive_name, headers)
        list_id = _get_drive_list_id(drive_id, headers)

        logging.info(
            "Historical ingest: site=%s drive=%s list=%s start=%s end=%s max=%s",
            site_id, drive_id, list_id,
            start_date.isoformat() if start_date else "none",
            end_date.isoformat() if end_date else "none",
            max_files,
        )

        items = _list_all_files_from_hods_list(
            site_id, list_id, headers, allowed_extensions,
            start_date=start_date, end_date=end_date, max_files=max_files,
        )
        logging.info("Historical ingest: %s files found, uploading…", len(items))

        uploaded_count = _upload_drive_items(
            blob_service_client, container_name, drive_id, site_id,
            items, headers, list_id, allowed_extensions,
        )

        result = {
            "uploaded": uploaded_count,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        }
        logging.info("Historical ingest complete: %s", result)
        return func.HttpResponse(json.dumps(result), status_code=200, mimetype="application/json")

    except Exception as exc:
        logging.exception("Historical ingest failed: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": str(exc)}),
            status_code=500,
            mimetype="application/json",
        )
