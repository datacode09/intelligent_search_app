#!/usr/bin/env python3
"""
Standalone historical SharePoint → Blob Storage load.

No timeout ceiling: runs to completion regardless of duration, making it
suitable for large backfills that would exceed the ~230-second HTTP response
limit of the IngestHistorical Azure Function trigger.

Reads the same env vars as the IngestHistorical function. CLI arguments
override the env-var defaults for the three date/cap parameters.

Usage:
    python scripts/historical_load.py [--start-date DATE] [--end-date DATE] [--max-files N]
                                      [--ca-bundle PATH]

    DATE format: ISO-8601, e.g. 2024-01-01T00:00:00Z

Required env vars:
    BLOB_STORAGE_CONNECTION_STRING
    SHAREPOINT_TENANT_ID
    SHAREPOINT_CLIENT_ID
    SHAREPOINT_CLIENT_SECRET
    SHAREPOINT_SITE_HOSTNAME
    SHAREPOINT_SITE_PATH

Optional env vars (same defaults as the function):
    BLOB_CONTAINER_NAME           (default: ingest-output)
    SHAREPOINT_LIBRARY_DRIVE_NAME (default: Documents)
    SHAREPOINT_SITE_ID            (optional override — skips _get_site_id Graph call)
    INGEST_FILE_EXTENSIONS        (default: .pdf)
    HISTORICAL_MAX_FILES          (default: 5000)
    REQUESTS_CA_BUNDLE            (path to a PEM CA bundle — see SSL section below)
    CURL_CA_BUNDLE                (alternative env var for the same purpose)

SSL / certificate issues:
    If connections fail with "certificate verify failed", your network is likely
    using an SSL-inspecting proxy or a private CA not in the default trust store
    (common in corporate and government environments).

    The correct fix is to supply the CA bundle file — never to disable verification.

    Two ways to provide the CA bundle path:

    1. Environment variable (apply it once for the whole session):
           export REQUESTS_CA_BUNDLE=/path/to/your-ca-bundle.crt
           python scripts/historical_load.py

    2. CLI argument (overrides the env var for this run):
           python scripts/historical_load.py --ca-bundle /path/to/your-ca-bundle.crt

    Common locations for CA bundles:
      - Azure Cloud Shell:   /opt/microsoft/azcopy/ca-bundle.crt
                             or: python -c "import certifi; print(certifi.where())"
      - Ubuntu/Debian:       /etc/ssl/certs/ca-certificates.crt
      - RHEL/CentOS:         /etc/pki/tls/certs/ca-bundle.crt
      - macOS (homebrew):    /etc/ssl/cert.pem
      - Windows (Git Bash):  C:/Program Files/Git/usr/ssl/certs/ca-bundle.crt
      - Corporate proxy CA:  ask your IT / platform team for the .crt or .pem file

    DO NOT use PYTHONHTTPSVERIFY=0 or requests.get(verify=False) — those silently
    disable certificate verification across the entire process and must never be
    used in production or committed to source control.
"""
import argparse
import datetime
import json
import logging
import os
import sys

# Add poc-hods-ingest to the module search path so we can import directly
# from function_app.py without duplicating any ingest logic here.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "poc-hods-ingest"))

from azure.storage.blob import BlobServiceClient  # noqa: E402 (after sys.path mutation)
from function_app import (  # noqa: E402
    _ensure_container,
    _get_allowed_extensions,
    _get_drive_id,
    _get_drive_list_id,
    _get_graph_token,
    _list_all_files_from_hods_list,
    _resolve_site_id,
    _upload_drive_items,
)


def _parse_date_arg(value: str, arg_name: str) -> datetime.datetime:
    try:
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except ValueError:
        print(f"error: --{arg_name}: cannot parse '{value}' as ISO-8601 date", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "One-off historical SharePoint → Blob Storage load with no timeout ceiling. "
            "Never reads or modifies the delta-state blob used by the incremental timer trigger."
        )
    )
    parser.add_argument(
        "--start-date",
        metavar="ISO8601",
        help="Only ingest files modified on or after this date (e.g. 2024-01-01T00:00:00Z).",
    )
    parser.add_argument(
        "--end-date",
        metavar="ISO8601",
        help="Only ingest files modified before this date.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        metavar="N",
        help="Cap the number of files processed. Defaults to HISTORICAL_MAX_FILES env var (or 5000).",
    )
    parser.add_argument(
        "--ca-bundle",
        metavar="PATH",
        help=(
            "Path to a PEM CA bundle file. Use when connections fail with "
            "'certificate verify failed' (e.g. corporate SSL-inspection proxy). "
            "Overrides REQUESTS_CA_BUNDLE env var. SSL verification is never disabled."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )

    ca_bundle = args.ca_bundle or os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("CURL_CA_BUNDLE")
    if ca_bundle:
        os.environ["REQUESTS_CA_BUNDLE"] = ca_bundle  # read by requests at call time
        os.environ["SSL_CERT_FILE"] = ca_bundle        # read by some other HTTP libs
        logging.info("Using CA bundle: %s", ca_bundle)

    start_date = _parse_date_arg(args.start_date, "start-date") if args.start_date else None
    end_date = _parse_date_arg(args.end_date, "end-date") if args.end_date else None

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
    max_files = args.max_files if args.max_files is not None else int(os.getenv("HISTORICAL_MAX_FILES", "5000"))

    missing = [
        name
        for name, val in [
            ("BLOB_STORAGE_CONNECTION_STRING", blob_connection_string),
            ("SHAREPOINT_TENANT_ID", tenant_id),
            ("SHAREPOINT_CLIENT_ID", client_id),
            ("SHAREPOINT_CLIENT_SECRET", client_secret),
            ("SHAREPOINT_SITE_HOSTNAME", site_hostname),
            ("SHAREPOINT_SITE_PATH", site_path),
        ]
        if not val
    ]
    if missing:
        print(f"error: missing required env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    try:
        blob_service_client = BlobServiceClient.from_connection_string(
            blob_connection_string,
            connection_verify=ca_bundle if ca_bundle else True,
        )
        _ensure_container(blob_service_client, container_name)

        token = _get_graph_token(tenant_id, client_id, client_secret)
        headers = {"Authorization": f"Bearer {token}"}
        site_id = _resolve_site_id(site_hostname, site_path, headers, site_id_override)
        drive_id = _get_drive_id(site_id, drive_name, headers)
        list_id = _get_drive_list_id(drive_id, headers)

        logging.info(
            "Historical load: site=%s drive=%s list=%s start=%s end=%s max=%s extensions=%s",
            site_id,
            drive_id,
            list_id,
            start_date.isoformat() if start_date else "none",
            end_date.isoformat() if end_date else "none",
            max_files,
            allowed_extensions,
        )

        items = _list_all_files_from_hods_list(
            site_id,
            list_id,
            headers,
            allowed_extensions,
            start_date=start_date,
            end_date=end_date,
            max_files=max_files,
        )
        logging.info("Found %s files to upload", len(items))

        uploaded_count = _upload_drive_items(
            blob_service_client,
            container_name,
            drive_id,
            site_id,
            items,
            headers,
            list_id,
            allowed_extensions,
        )

        result = {
            "uploaded": uploaded_count,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        }
        logging.info("Historical load complete")
        print(json.dumps(result))

    except Exception as exc:
        logging.exception("Historical load failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
