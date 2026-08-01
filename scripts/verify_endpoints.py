#!/usr/bin/env python3
"""
Connectivity check for the two endpoints used by historical_load.py.

Verifies — without transferring any documents — that credentials are valid
and both the source (Microsoft Graph / SharePoint) and destination (Azure
Blob Storage) are reachable from this machine.

Checks (run in order, Graph checks stop on first failure):
  1. Microsoft Graph authentication  — service principal token acquired
  2. SharePoint site reachable       — site ID resolved
  3. SharePoint document library     — drive ID resolved
  4. Azure Blob Storage reachable    — container accessible (independent of Graph)

Usage:
    python scripts/verify_endpoints.py [--ca-bundle PATH]

    # Windows example with Netskope cert:
    python scripts\\verify_endpoints.py --ca-bundle "C:\\ProgramData\\Netskope\\STAgent\\data\\netskope-cert.pem"

Same required env vars as historical_load.py:
    BLOB_STORAGE_CONNECTION_STRING
    SHAREPOINT_TENANT_ID
    SHAREPOINT_CLIENT_ID
    SHAREPOINT_CLIENT_SECRET
    SHAREPOINT_SITE_HOSTNAME
    SHAREPOINT_SITE_PATH

Optional:
    BLOB_CONTAINER_NAME              (default: ingest-output)
    SHAREPOINT_LIBRARY_DRIVE_NAME    (default: Documents)
    SHAREPOINT_SITE_ID               (skip site-ID Graph call if already known)
    REQUESTS_CA_BUNDLE               (CA bundle path — overridden by --ca-bundle)
    CURL_CA_BUNDLE                   (fallback CA bundle env var)

Exit codes:
    0 — all checks passed
    1 — one or more checks failed, or required env vars are missing

SSL / Netskope:
    If you see "certificate verify failed", pass your Netskope (or corporate)
    CA certificate via --ca-bundle or REQUESTS_CA_BUNDLE. Common paths:

      Windows: C:\\ProgramData\\Netskope\\STAgent\\data\\netskope-cert.pem
               (or export from certmgr.msc → Trusted Root CAs → Netskope)
      macOS:   /Library/Application Support/Netskope/STAgent/data/netskope-cert.pem
               (or export from Keychain Access → System Roots → Netskope)
"""
import argparse
import os
import sys

# Add poc-hods-ingest to the module search path so we can reuse function_app helpers.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "poc-hods-ingest"))

from azure.storage.blob import BlobServiceClient  # noqa: E402
from function_app import (  # noqa: E402
    _ensure_container,
    _get_drive_id,
    _get_graph_token,
    _resolve_site_id,
)

_PASS = "[PASS]"
_FAIL = "[FAIL]"


def _check(label, fn):
    """Run fn(); print PASS/FAIL and return the result (or None on failure)."""
    try:
        result = fn()
        print(f"{_PASS} {label}")
        return result
    except Exception as exc:
        print(f"{_FAIL} {label}")
        print(f"       {exc}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify connectivity to the SharePoint source and Azure Blob Storage "
            "destination before running historical_load.py."
        )
    )
    parser.add_argument(
        "--ca-bundle",
        metavar="PATH",
        help=(
            "Path to a PEM CA bundle (e.g. Netskope cert). "
            "Use when connections fail with 'certificate verify failed'. "
            "Overrides REQUESTS_CA_BUNDLE env var. SSL verification is never disabled."
        ),
    )
    args = parser.parse_args()

    ca_bundle = args.ca_bundle or os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("CURL_CA_BUNDLE")
    if ca_bundle:
        os.environ["REQUESTS_CA_BUNDLE"] = ca_bundle
        os.environ["SSL_CERT_FILE"] = ca_bundle
        print(f"CA bundle : {ca_bundle}")
        print()

    blob_conn = os.getenv("BLOB_STORAGE_CONNECTION_STRING")
    container_name = os.getenv("BLOB_CONTAINER_NAME", "ingest-output")
    tenant_id = os.getenv("SHAREPOINT_TENANT_ID")
    client_id = os.getenv("SHAREPOINT_CLIENT_ID")
    client_secret = os.getenv("SHAREPOINT_CLIENT_SECRET")
    site_hostname = os.getenv("SHAREPOINT_SITE_HOSTNAME")
    site_path = os.getenv("SHAREPOINT_SITE_PATH")
    site_id_override = os.getenv("SHAREPOINT_SITE_ID")
    drive_name = os.getenv("SHAREPOINT_LIBRARY_DRIVE_NAME", "Documents")

    missing = [
        name
        for name, val in [
            ("BLOB_STORAGE_CONNECTION_STRING", blob_conn),
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

    print("Source checks (Microsoft Graph / SharePoint)")
    print("-" * 44)

    failed = False

    token = _check(
        "Graph auth — service principal token",
        lambda: _get_graph_token(tenant_id, client_id, client_secret),
    )
    if not token:
        failed = True
        print("       (skipping remaining Graph checks)")
    else:
        headers = {"Authorization": f"Bearer {token}"}

        site_id = _check(
            f"SharePoint site — {site_hostname}{site_path}",
            lambda: _resolve_site_id(site_hostname, site_path, headers, site_id_override),
        )
        if not site_id:
            failed = True
            print("       (skipping document library check)")
        else:
            drive_id = _check(
                f"Document library — '{drive_name}'",
                lambda: _get_drive_id(site_id, drive_name, headers),
            )
            if not drive_id:
                failed = True

    print()
    print("Destination checks (Azure Blob Storage)")
    print("-" * 40)

    def _blob_check():
        client = BlobServiceClient.from_connection_string(
            blob_conn,
            connection_verify=ca_bundle if ca_bundle else True,
        )
        _ensure_container(client, container_name)
        return client

    blob_ok = _check(f"Blob Storage — container '{container_name}'", _blob_check)
    if not blob_ok:
        failed = True

    print()
    if failed:
        print("Result: FAILED — fix the errors above before running historical_load.py.")
        sys.exit(1)
    else:
        print("Result: ALL CHECKS PASSED — ready to run historical_load.py.")


if __name__ == "__main__":
    main()
