"""
Creates the Azure AI Search data source, skillset, index, and indexer
from the definitions in infra/search-indexer.json and infra/search-index.json.

Run this ONCE after deploying Azure infrastructure:
    python scripts/create-indexer.py

Prerequisites:
    pip install azure-search-documents azure-identity requests
    az login
"""

import json
import os
import sys

import requests
from azure.identity import DefaultAzureCredential

SEARCH_ENDPOINT = os.environ.get("AZURE_SEARCH_ENDPOINT", "").rstrip("/")
OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_BASE_URL", "").rstrip("/")
STORAGE_CONN    = os.environ.get("BLOB_STORAGE_CONNECTION_STRING", "")

if not SEARCH_ENDPOINT or not OPENAI_ENDPOINT or not STORAGE_CONN:
    print("ERROR: Set AZURE_SEARCH_ENDPOINT, AZURE_OPENAI_BASE_URL, BLOB_STORAGE_CONNECTION_STRING")
    sys.exit(1)

# Use Entra auth (az login / Managed Identity)
credential = DefaultAzureCredential()
token = credential.get_token("https://search.azure.com/.default").token
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {token}",
}

API_VER = "2024-07-01"

def call(method: str, path: str, body: dict) -> dict:
    url = f"{SEARCH_ENDPOINT}/{path}?api-version={API_VER}"
    resp = getattr(requests, method)(url, headers=headers, json=body, timeout=60)
    if resp.status_code not in (200, 201, 204):
        print(f"  FAILED [{resp.status_code}]: {resp.text[:400]}")
        sys.exit(1)
    print(f"  OK [{resp.status_code}]")
    return resp.json() if resp.content else {}


# Load templates
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(root, "infra", "search-index.json"))   as f: index_def    = json.load(f)
with open(os.path.join(root, "infra", "search-indexer.json")) as f: indexer_defs = json.load(f)

# Inject real values
openai_ep = OPENAI_ENDPOINT.replace("/openai/v1", "").replace("/v1", "")
indexer_defs["dataSource"]["credentials"]["connectionString"] = STORAGE_CONN
indexer_defs["skillset"]["skills"][1]["resourceUri"]          = openai_ep
index_def["vectorSearch"]["vectorizers"][0]["azureOpenAIParameters"]["resourceUri"] = openai_ep

print("\n1. Creating index...")
call("put", f"indexes/{index_def['name']}", index_def)

print("2. Creating data source...")
call("put", f"datasources/{indexer_defs['dataSource']['name']}", indexer_defs["dataSource"])

print("3. Creating skillset...")
call("put", f"skillsets/{indexer_defs['skillset']['name']}", indexer_defs["skillset"])

print("4. Creating indexer...")
call("put", f"indexers/{indexer_defs['indexer']['name']}", indexer_defs["indexer"])

print("\nDone! The indexer will run on its first trigger (or run it manually in the Azure Portal).")
print(f"Search endpoint: {SEARCH_ENDPOINT}")
print(f"Index name:      {index_def['name']}")
