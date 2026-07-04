"""SharePoint → Azure Blob Storage incremental ingest — Azure Function App (Python v2)."""
from __future__ import annotations

import dataclasses
import datetime
import json
import logging
import os
import re
import time
from collections import deque
from typing import Callable, Dict, Iterable, List, NamedTuple, Optional, TypeVar

import azure.functions as func
import requests
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient

# ── Constants ─────────────────────────────────────────────────────────────────

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_DOWNLOAD_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB streaming chunks
_BLOB_METADATA_MAX_BYTES = 8000          # Azure hard limit is 8192; leave margin

_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)

_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
)

_SYSTEM_FIELDS: frozenset = frozenset({
    "id", "ID", "ContentTypeId", "FileRef", "FileDirRef", "FileLeafRef",
    "FSObjType", "UniqueId", "owshiddenversion", "ProgId", "ScopeId",
    "InstanceID", "Order", "GUID", "WorkflowVersion", "WorkflowInstanceID",
    "ParentVersionString", "ParentLeafName",
})

app = func.FunctionApp()

# ── Configuration ─────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class _IngestConfig:
    blob_connection_string: str
    container_name: str
    tenant_id: str
    client_id: str
    client_secret: str
    site_hostname: str
    site_path: str
    drive_name: str
    max_files: int
    metadata_columns: Optional[frozenset]
    start_date: datetime.datetime
    site_id_override: Optional[str] = None


def _load_config() -> _IngestConfig:
    missing: List[str] = []

    def _require(name: str) -> str:
        value = (os.getenv(name) or "").strip()
        if not value:
            missing.append(name)
        return value

    blob_connection_string = _require("BLOB_STORAGE_CONNECTION_STRING")
    tenant_id              = _require("SHAREPOINT_TENANT_ID")
    client_id              = _require("SHAREPOINT_CLIENT_ID")
    client_secret          = _require("SHAREPOINT_CLIENT_SECRET")
    site_hostname          = _require("SHAREPOINT_SITE_HOSTNAME")
    site_path              = _require("SHAREPOINT_SITE_PATH")

    if missing:
        raise RuntimeError("Missing required app settings: %s" % ", ".join(missing))

    columns_raw = (os.getenv("INGEST_METADATA_COLUMNS") or "").strip()
    metadata_columns: Optional[frozenset] = (
        frozenset(c.strip() for c in columns_raw.split(",") if c.strip())
        if columns_raw else None
    )

    return _IngestConfig(
        blob_connection_string=blob_connection_string,
        container_name=(os.getenv("BLOB_CONTAINER_NAME") or "ingest-output"),
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        site_hostname=site_hostname,
        site_path=site_path,
        drive_name=(os.getenv("SHAREPOINT_LIBRARY_DRIVE_NAME") or "Documents"),
        max_files=int(os.getenv("INGEST_MAX_FILES_PER_RUN") or "500"),
        metadata_columns=metadata_columns,
        start_date=_parse_last_sync(os.getenv("INGEST_START_DATE") or ""),
        site_id_override=os.getenv("SHAREPOINT_SITE_ID") or None,
    )

# ── HTTP / Graph helpers ──────────────────────────────────────────────────────

_T = TypeVar("_T")


def _retry(
    call: Callable[[], _T],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    retry_on: tuple = (requests.exceptions.RequestException,),
) -> _T:
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except retry_on as exc:
            if attempt == attempts:
                raise
            logging.warning("Retrying after error (attempt %d/%d): %s", attempt, attempts, exc)
            time.sleep(base_delay * (2 ** (attempt - 1)))
    raise AssertionError("unreachable")


def _graph_get(url: str, headers: Dict[str, str]) -> Dict:
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()


def _get_graph_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    response = requests.post(
        "https://login.microsoftonline.com/%s/oauth2/v2.0/token" % tenant_id,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Graph token response did not include access_token")
    return token

# ── Datetime helpers ──────────────────────────────────────────────────────────

def _parse_last_sync(
    last_sync_raw: Optional[str],
    *,
    default: Optional[datetime.datetime] = None,
) -> datetime.datetime:
    effective_default = default if default is not None else _EPOCH
    raw = (last_sync_raw or "").strip()
    if not raw:
        return effective_default
    for fmt in _DATETIME_FORMATS:
        try:
            parsed = datetime.datetime.strptime(raw, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed.astimezone(datetime.timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.astimezone(datetime.timezone.utc)
    except ValueError:
        logging.warning(
            "Unrecognized last-sync format %r; using default %s",
            raw, effective_default.isoformat(),
        )
        return effective_default

# ── SharePoint site and drive discovery ──────────────────────────────────────

def _get_site_id(hostname: str, site_path: str, headers: Dict[str, str]) -> str:
    site_json = _graph_get("%s/sites/%s:%s" % (_GRAPH_BASE, hostname, site_path), headers)
    site_id = site_json.get("id")
    if not site_id:
        raise RuntimeError("Unable to resolve SharePoint site id for %s%s" % (hostname, site_path))
    return site_id


def _normalize_drive_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


def _get_drive_id(site_id: str, drive_name: str, headers: Dict[str, str]) -> str:
    requested = _normalize_drive_name(drive_name)
    first_drive_id: Optional[str] = None
    next_link: Optional[str] = "%s/sites/%s/drives?$top=200" % (_GRAPH_BASE, site_id)
    while next_link:
        page = _graph_get(next_link, headers)
        for drive in page.get("value", []):
            drive_id = drive.get("id")
            if not drive_id:
                continue
            if _normalize_drive_name(drive.get("name", "")) == requested:
                return drive_id
            if first_drive_id is None:
                first_drive_id = drive_id
        next_link = page.get("@odata.nextLink")
    if first_drive_id:
        return first_drive_id
    raise RuntimeError("Drive '%s' not found in site '%s'" % (drive_name, site_id))


def _get_drive_list_id(drive_id: str, headers: Dict[str, str]) -> str:
    """Return the SharePoint list GUID for the document library behind a drive."""
    drive_json = _graph_get(
        "%s/drives/%s?$select=id,sharePointIds" % (_GRAPH_BASE, drive_id), headers
    )
    list_id = (drive_json.get("sharePointIds") or {}).get("listId")
    if not list_id:
        raise RuntimeError("Could not resolve SharePoint list ID for drive '%s'" % drive_id)
    return list_id

# ── Drive item listing ────────────────────────────────────────────────────────

def _list_all_items(drive_id: str, headers: Dict[str, str]) -> Iterable[Dict]:
    """Breadth-first traversal yielding every item in a drive, including nested folders."""
    queue: deque = deque(["root"])
    while queue:
        parent = queue.popleft()
        next_link: Optional[str] = (
            "%s/drives/%s/items/%s/children?$top=200" % (_GRAPH_BASE, drive_id, parent)
        )
        while next_link:
            page = _graph_get(next_link, headers)
            for item in page.get("value", []):
                yield item
                if "folder" in item:
                    child_id = item.get("id")
                    if child_id:
                        queue.append(child_id)
            next_link = page.get("@odata.nextLink")

# ── Blob naming and metadata helpers ─────────────────────────────────────────

def _to_blob_name(file_name: str) -> str:
    base = os.path.basename((file_name or "").strip())
    normalized = re.sub(r"_+", "_", re.sub(r"[^0-9A-Za-z._-]+", "_", base)).strip("._-")
    return normalized or "file"


def _is_system_field(key: str) -> bool:
    return key.startswith(("@", "_")) or key in _SYSTEM_FIELDS


def _sanitize_metadata_key(key: str) -> str:
    """Return a valid Azure Blob metadata key (must satisfy C# identifier rules)."""
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", key)
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized or "_field"


def _to_blob_metadata_value(raw_value: object) -> str:
    """Serialise a SharePoint field value to an ASCII blob metadata string."""
    if raw_value is None:
        return ""
    if isinstance(raw_value, list):
        values: List[str] = []
        for elem in raw_value:
            if isinstance(elem, dict):
                display = (
                    elem.get("LookupValue") or elem.get("lookupValue") or elem.get("Label")
                )
                values.append(str(display) if display is not None else str(elem))
            else:
                values.append(str(elem))
        text = json.dumps(values, ensure_ascii=True)
    elif isinstance(raw_value, dict):
        # LookupValue: regular lookup columns
        # Label: taxonomy / managed metadata columns ({"Label": "...", "TermGuid": "...", "WssId": ...})
        display = (
            raw_value.get("LookupValue") or raw_value.get("lookupValue") or raw_value.get("Label")
        )
        text = str(display) if display is not None else str(raw_value)
    else:
        text = str(raw_value)
    return text.encode("ascii", errors="ignore").decode("ascii")


def _trim_metadata(metadata: Dict[str, str], item_id: str = "") -> Dict[str, str]:
    """Drop longest values first until total bytes fit within Azure's 8 KB limit."""
    if sum(len(k) + len(v) for k, v in metadata.items()) <= _BLOB_METADATA_MAX_BYTES:
        return metadata
    result: Dict[str, str] = {k: v for k, v in metadata.items() if k == "Modified"}
    candidates = sorted(
        ((k, v) for k, v in metadata.items() if k != "Modified"),
        key=lambda kv: len(kv[1]),
        reverse=True,
    )
    for k, v in candidates:
        if sum(len(kk) + len(vv) for kk, vv in result.items()) + len(k) + len(v) <= _BLOB_METADATA_MAX_BYTES:
            result[k] = v
        else:
            logging.warning(
                "Metadata key %r dropped for item %s: would exceed 8 KB limit", k, item_id
            )
    return result


def _build_blob_metadata(
    item: Dict,
    fields: Dict,
    *,
    metadata_columns: Optional[frozenset],
) -> Dict[str, str]:
    """Build the blob metadata dict from drive item properties and SharePoint fields."""
    metadata: Dict[str, str] = {}
    metadata["Modified"] = _to_blob_metadata_value(item.get("lastModifiedDateTime"))
    if created := item.get("createdDateTime"):
        metadata["Created"] = _to_blob_metadata_value(created)
    if (size := item.get("size")) is not None:
        metadata["Size"] = str(size)
    if created_by := ((item.get("createdBy") or {}).get("user") or {}).get("displayName"):
        metadata["CreatedBy"] = _to_blob_metadata_value(created_by)
    if modified_by := ((item.get("lastModifiedBy") or {}).get("user") or {}).get("displayName"):
        metadata["ModifiedBy"] = _to_blob_metadata_value(modified_by)
    for key, value in fields.items():
        if _is_system_field(key):
            continue
        if metadata_columns is not None and key not in metadata_columns:
            continue
        if blob_val := _to_blob_metadata_value(value):
            metadata[_sanitize_metadata_key(key)] = blob_val
    return metadata

# ── Content transfer ──────────────────────────────────────────────────────────

def _download_and_upload(
    content_url: str,
    headers: Dict[str, str],
    blob_client,
    metadata: Dict[str, str],
) -> None:
    with requests.get(content_url, headers=headers, stream=True, timeout=120) as response:
        response.raise_for_status()
        blob_client.upload_blob(
            response.iter_content(chunk_size=_DOWNLOAD_CHUNK_SIZE),
            overwrite=True,
            metadata=metadata or None,
        )

# ── Sync-state persistence ────────────────────────────────────────────────────

def _read_last_sync(
    blob_service_client: BlobServiceClient,
    container_name: str,
    *,
    default: datetime.datetime,
) -> datetime.datetime:
    try:
        blob = blob_service_client.get_blob_client(container=container_name, blob="last-sync")
        raw = blob.download_blob().readall().decode("utf-8")
        logging.info("Last-sync blob value: %s", raw)
        return _parse_last_sync(raw, default=default)
    except Exception:
        logging.info("No last-sync blob found; starting from %s", default.isoformat())
        return default


def _write_last_sync(
    blob_service_client: BlobServiceClient,
    container_name: str,
    sync_time: datetime.datetime,
) -> None:
    blob = blob_service_client.get_blob_client(container=container_name, blob="last-sync")
    blob.upload_blob(sync_time.isoformat(), overwrite=True)
    logging.info("Updated last-sync to %s", sync_time.isoformat())

# ── Core sync logic ───────────────────────────────────────────────────────────

def _fetch_item_fields(
    drive_id: str,
    item_id: str,
    site_id: str,
    list_id: str,
    headers: Dict[str, str],
) -> Dict:
    """Fetch all SharePoint list-item fields for a drive item (two-step: ID resolution then fields)."""
    sp_id_json = _graph_get(
        "%s/drives/%s/items/%s?$expand=listItem($select=id)" % (_GRAPH_BASE, drive_id, item_id),
        headers,
    )
    sp_item_id = (sp_id_json.get("listItem") or {}).get("id")
    if not sp_item_id:
        logging.warning("Could not resolve SP list item ID for drive item %s", item_id)
        return {}
    fields_json = _graph_get(
        "%s/sites/%s/lists/%s/items/%s?$expand=fields"
        % (_GRAPH_BASE, site_id, list_id, sp_item_id),
        headers,
    )
    fields = fields_json.get("fields") or {}
    logging.debug("Item %s field keys: %s", item_id, sorted(fields.keys()))
    return fields


class _UploadResult(NamedTuple):
    uploaded: int
    sync_point: Optional[datetime.datetime]


def _upload_changed_files(
    *,
    blob_service_client: BlobServiceClient,
    container_name: str,
    drive_id: str,
    site_id: str,
    last_sync: datetime.datetime,
    headers: Dict[str, str],
    max_files: int = 500,
    metadata_columns: Optional[frozenset] = None,
) -> _UploadResult:
    list_id = _get_drive_list_id(drive_id, headers)
    uploaded = 0
    earliest: Optional[datetime.datetime] = None
    latest: Optional[datetime.datetime] = None
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
        item_id   = item.get("id")
        file_name = item.get("name")
        if not item_id or not file_name:
            continue

        blob_client  = blob_service_client.get_blob_client(
            container=container_name, blob=_to_blob_name(file_name)
        )
        fields       = _fetch_item_fields(drive_id, item_id, site_id, list_id, headers)
        metadata     = _build_blob_metadata(item, fields, metadata_columns=metadata_columns)
        metadata     = _trim_metadata(metadata, item_id)
        content_url  = "%s/drives/%s/items/%s/content" % (_GRAPH_BASE, drive_id, item_id)

        logging.info("Uploading %s (item %s) with %d metadata keys", file_name, item_id, len(metadata))
        _retry(lambda: _download_and_upload(content_url, headers, blob_client, metadata))

        uploaded += 1
        earliest  = modified_at if earliest is None else min(earliest, modified_at)
        latest    = modified_at if latest   is None else max(latest, modified_at)

    return _UploadResult(uploaded=uploaded, sync_point=earliest if cap_hit else latest)

# ── Timer-triggered entry point ───────────────────────────────────────────────

@app.timer_trigger(
    schedule=os.getenv("INGEST_SCHEDULE_CRON", "0 0 * * * *"),
    arg_name="myTimer",
    run_on_startup=False,
    use_monitor=False,
)
def Ingest(myTimer: func.TimerRequest) -> None:
    if myTimer.past_due:
        logging.warning("Timer is past due; running now")

    now_utc = datetime.datetime.now(datetime.timezone.utc)

    try:
        cfg = _load_config()
    except RuntimeError as exc:
        logging.error("%s", exc)
        return

    # TODO [ISSUE-7]: Replace connection-string auth with Managed Identity.
    # Swap to DefaultAzureCredential + BLOB_STORAGE_ACCOUNT_URL for zero-secret auth.
    blob_service_client = BlobServiceClient.from_connection_string(cfg.blob_connection_string)

    last_sync = _read_last_sync(
        blob_service_client, cfg.container_name, default=cfg.start_date
    )
    logging.info("Ingesting files modified after %s", last_sync.isoformat())

    try:
        blob_service_client.get_container_client(cfg.container_name).create_container()
    except ResourceExistsError:
        pass  # expected on every run after the first

    try:
        token    = _get_graph_token(cfg.tenant_id, cfg.client_id, cfg.client_secret)
        headers  = {"Authorization": "Bearer %s" % token}
        site_id  = cfg.site_id_override or _get_site_id(cfg.site_hostname, cfg.site_path, headers)
        drive_id = _get_drive_id(site_id, cfg.drive_name, headers)

        result = _upload_changed_files(
            blob_service_client=blob_service_client,
            container_name=cfg.container_name,
            drive_id=drive_id,
            site_id=site_id,
            last_sync=last_sync,
            headers=headers,
            max_files=cfg.max_files,
            metadata_columns=cfg.metadata_columns,
        )
        logging.info("Sync complete: %d file(s) uploaded", result.uploaded)
    except Exception:
        logging.exception("Sync failed; last-sync will not be advanced")
        return

    try:
        _write_last_sync(
            blob_service_client, cfg.container_name, result.sync_point or now_utc
        )
    except Exception:
        logging.exception("Failed to write last-sync blob")
