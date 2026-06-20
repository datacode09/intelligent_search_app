# Integration Testing Guide — HODS Intelligent Search

## Overview

Integration tests verify that the four components (Ingest, AI Search, API, UI) work correctly together against real (or realistically emulated) Azure services. They go beyond unit tests — they test actual data flowing through the pipeline end-to-end.

**Prerequisites:** A deployed dev Azure environment (see `docs/DEV-GUIDE-INFRA.md`). Integration tests require live Azure resources and cannot run with mocks alone.

---

## Test Environment Setup

### 1. Deploy the dev environment

Follow `docs/DEV-GUIDE-INFRA.md` to deploy all resources to a dedicated integration test resource group (e.g., `hods-int-rg`). Keep it separate from production.

### 2. Seed test data

Upload a small controlled set of test documents to the Blob Storage `ingest-output` container. These are used to assert predictable search results.

```python
# scripts/seed_test_data.py
from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
client = BlobServiceClient(account_url="https://<storage>.blob.core.windows.net", credential=credential)
container = client.get_container_client("ingest-output")

test_docs = [
    {
        "name": "HO-Safety-Grounding-001.pdf",
        "content": b"%PDF-1.4 ... (binary content) ...",
        "metadata": {
            "DocumentName": "HO-Safety-Grounding-001",
            "Prefix": "HO",
            "ContentType": "Safety Equipment and Practices",
            "SourceUrl": "https://tenant.sharepoint.com/sites/HODS/Documents/HO-Safety-Grounding-001.pdf",
        }
    },
    {
        "name": "AL-Corporate-Standards-002.docx",
        "content": b"...",
        "metadata": {
            "DocumentName": "AL-Corporate-Standards-002",
            "Prefix": "AL",
            "ContentType": "Corporate Standards",
            "SourceUrl": "https://tenant.sharepoint.com/sites/HODS/Documents/AL-Corporate-Standards-002.docx",
        }
    }
]

for doc in test_docs:
    container.upload_blob(doc["name"], doc["content"], metadata=doc["metadata"], overwrite=True)

print("Test documents uploaded.")
```

### 3. Run the indexer

After uploading test blobs, trigger the indexer to pick them up:

```bash
# Using Azure CLI
az search indexer run \
  --name hods-indexer \
  --service-name <search-service-name> \
  --resource-group hods-int-rg

# Wait for indexer to complete (poll status)
az search indexer show \
  --name hods-indexer \
  --service-name <search-service-name> \
  --resource-group hods-int-rg \
  --query "lastResult.status" -o tsv
# Expected: success
```

---

## Integration Test Scenarios

### IT-01 — Ingest to Index Pipeline

**Verifies:** Documents uploaded to Blob Storage appear in AI Search index.

**Steps:**
1. Count documents in AI Search index before upload.
2. Upload a new test blob to `ingest-output` with known metadata.
3. Trigger the indexer (or wait for scheduled run).
4. Poll indexer status until `success`.
5. Query the index for the document by `title`.

**Expected result:** Document count increases by 1; the new document is retrievable by title.

```python
# scripts/test_ingest_to_index.py
import time
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient

credential = DefaultAzureCredential()
client = SearchClient(
    endpoint="https://<search>.search.windows.net",
    index_name="hods-index",
    credential=credential,
    audience="https://search.azure.com",
)

# Poll until the test document appears
for attempt in range(10):
    results = list(client.search("HO-Safety-Grounding-001", select=["title"]))
    if any(r["title"] == "HO-Safety-Grounding-001" for r in results):
        print("PASS: Document found in index.")
        break
    print(f"Attempt {attempt+1}: not indexed yet, waiting 30s...")
    time.sleep(30)
else:
    print("FAIL: Document not found in index after 5 minutes.")
```

---

### IT-02 — API Search Returns Seeded Document

**Verifies:** `POST /query` returns the seeded test document with expected metadata.

**Prerequisites:** IT-01 passed; API deployed; valid Bearer token available.

**Steps:**
1. Obtain a Bearer token from Entra ID for the test user or service principal.
2. POST `{"query": "safety grounding"}` to `/query`.
3. Assert the response contains the seeded `HO-Safety-Grounding-001` document.
4. Assert `Prefix` is `"HO"`, `ContentType` includes `"Safety Equipment and Practices"`.
5. Assert `Highlights` is non-empty (extractive captions working).

```python
import requests

API_URL = "https://<api-app>.azurewebsites.net"
TOKEN = "<bearer-token>"  # obtain via az account get-access-token or MSAL

resp = requests.post(
    f"{API_URL}/query",
    json={"query": "safety grounding", "keywords": [], "filter": []},
    headers={"Authorization": f"Bearer {TOKEN}"},
)
assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

data = resp.json()
assert data["count"] > 0, "Expected at least one result"

titles = [r["DocumentName"] for r in data["results"]]
assert "HO-Safety-Grounding-001" in titles, f"Expected document not found. Got: {titles}"

doc = next(r for r in data["results"] if r["DocumentName"] == "HO-Safety-Grounding-001")
assert doc["Prefix"] == "HO"
assert len(doc["Highlights"]) > 0, "Expected extractive captions"
print("PASS: IT-02 API search returns seeded document.")
```

---

### IT-03 — Content Type Filter

**Verifies:** OData filter reduces result set to the correct content type.

**Steps:**
1. POST `{"query": "*", "filter": [{"key": "ContentType", "value": "Corporate Standards"}]}`.
2. Assert all returned documents have `ContentType` containing `"Corporate Standards"`.
3. Assert `HO-Safety-Grounding-001` (Safety type) is NOT in the results.

```python
resp = requests.post(
    f"{API_URL}/query",
    json={"query": "*", "keywords": [], "filter": [{"key": "ContentType", "value": "Corporate Standards"}]},
    headers={"Authorization": f"Bearer {TOKEN}"},
)
assert resp.status_code == 200
data = resp.json()
for doc in data["results"]:
    ct = doc.get("ContentType") or []
    if isinstance(ct, str):
        ct = [ct]
    assert "Corporate Standards" in ct, f"Document {doc['DocumentName']} has wrong ContentType: {ct}"
print("PASS: IT-03 ContentType filter works correctly.")
```

---

### IT-04 — Query Optimizer Returns Valid Structure

**Verifies:** `/OptimizeHybridQueries` returns `OptimizedQuery` (string) and `keywords` (list).

```python
resp = requests.post(
    f"{API_URL}/OptimizeHybridQueries",
    json={"text": "how do I safely ground high voltage equipment"},
    headers={"Authorization": f"Bearer {TOKEN}"},
)
assert resp.status_code == 200
data = resp.json()
assert "OptimizedQuery" in data and isinstance(data["OptimizedQuery"], str)
assert "keywords" in data and isinstance(data["keywords"], list)
assert len(data["OptimizedQuery"]) > 0
print("PASS: IT-04 Optimizer returns valid structure.")
```

---

### IT-05 — Unauthorized Request Returns 401

**Verifies:** Endpoints reject requests without a valid Bearer token.

```python
for endpoint, body in [
    ("/query", {"query": "test"}),
    ("/OptimizeHybridQueries", {"text": "test"}),
]:
    resp = requests.post(f"{API_URL}{endpoint}", json=body)
    assert resp.status_code in (401, 403), \
        f"Expected 401/403 for {endpoint} without auth, got {resp.status_code}"
print("PASS: IT-05 Unauthenticated requests are rejected.")
```

---

### IT-06 — Ingest Timer Trigger Runs Without Error

**Verifies:** The Azure Function timer trigger executes and writes to Blob Storage without exceptions.

**Steps:**
1. Manually trigger the function via the Azure Portal or Admin API.
2. Check Application Insights for errors in the last 5 minutes.
3. Check Blob Storage for an updated `last_sync.txt`.

```bash
# Manually trigger
curl -X POST https://<function-app>.azurewebsites.net/admin/functions/hods_ingest_timer \
  -H "x-functions-key: <master-key>" \
  -H "Content-Type: application/json" \
  -d '{}'

# Check last_sync.txt was updated
az storage blob download \
  --account-name <storage-account> \
  --container-name ingest-output \
  --name last_sync.txt \
  --file /tmp/last_sync.txt \
  --auth-mode login
cat /tmp/last_sync.txt
# Expected: ISO8601 timestamp close to current time
```

---

## Running All Integration Tests

Wrap all integration tests in a single script with clear pass/fail output:

```bash
cd intelligent_search_app
pip install requests azure-identity azure-search-documents

# Set environment
export API_URL="https://<api-app>.azurewebsites.net"
export SEARCH_ENDPOINT="https://<search>.search.windows.net"
export STORAGE_ACCOUNT="<storage-account-name>"

# Get a token (requires az login)
export TOKEN=$(az account get-access-token --resource "api://<client-id>" --query accessToken -o tsv)

python scripts/test_ingest_to_index.py   # IT-01
python scripts/test_api_integration.py   # IT-02 through IT-05
```

---

## Integration Test Checklist

| ID | Test | Pass Criteria |
|---|---|---|
| IT-01 | Ingest → Index | Blob appears in AI Search index within 5 minutes of upload |
| IT-02 | API search | `/query` returns seeded document with correct metadata and highlights |
| IT-03 | ContentType filter | OData filter restricts results to matching content type only |
| IT-04 | Query optimizer | `/OptimizeHybridQueries` returns `OptimizedQuery` string + `keywords` list |
| IT-05 | Auth rejection | Requests without Bearer token return 401/403 |
| IT-06 | Timer trigger | Function runs without error; `last_sync.txt` updated in Blob |
| IT-07 | DocumentUrl | `SourceUrl` populated on all indexed documents (after ISSUE-4 fix) |
| IT-08 | MSAL in UI | `Authorization` header present on all API calls from browser (after ISSUE-5 fix) |

---

## Teardown After Integration Tests

```bash
# Remove seeded test blobs
az storage blob delete-batch \
  --source ingest-output \
  --account-name <storage-account> \
  --pattern "HO-Safety-*" \
  --auth-mode login

az storage blob delete-batch \
  --source ingest-output \
  --account-name <storage-account> \
  --pattern "AL-Corporate-*" \
  --auth-mode login
```
