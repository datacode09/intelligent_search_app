# Development & Testing Guide — API Component (`hydro-one-hods-api`)

## Overview

The API is a FastAPI (Python 3.12) application served by uvicorn. It exposes two authenticated endpoints: `/query` (hybrid semantic search via Azure AI Search) and `/OptimizeHybridQueries` (GPT-4o query optimization). Authentication is Entra ID JWT Bearer token validation.

**Technology:** Python 3.12, FastAPI 0.115, uvicorn, `azure-search-documents`, `openai`, `azure-identity`, `python-jose`

---

## Local Development Setup

### 1. Prerequisites

- Python 3.12
- Access to Azure AI Search and Azure OpenAI resources (or mock them — see Testing section)

### 2. Create a virtual environment

```bash
cd hydro-one-hods-api
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install pytest pytest-cov httpx  # test dependencies
```

### 3. Configure environment variables

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

`.env` contents:

```env
AZURE_TENANT_ID=<your-tenant-id>
AZURE_CLIENT_ID=<api-app-registration-client-id>
AZURE_SEARCH_ENDPOINT=https://<search-service-name>.search.windows.net
AZURE_SEARCH_INDEX_NAME=hods-index
AZURE_SEARCH_SEMANTIC_CONFIGURATION=hods-index-semantic-configuration
AZURE_SEARCH_TOKEN_SCOPE=https://search.azure.com/.default
AZURE_OPENAI_BASE_URL=https://<openai-resource-name>.openai.azure.com/openai/v1/
AZURE_OPENAI_API_VERSION=preview
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
AZURE_OPENAI_TOKEN_SCOPE=https://cognitiveservices.azure.com/.default
ALLOWED_ORIGINS=http://localhost:5173
```

> **Never commit `.env`.** It is listed in `.gitignore`.

### 4. Run the API locally

```bash
uvicorn app.main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`. Swagger UI is at `http://localhost:8000/docs`.

#### Authenticate locally

`DefaultAzureCredential` tries several auth methods in order. For local development:

```bash
az login
```

This satisfies `DefaultAzureCredential` and grants the API access to Azure AI Search and Azure OpenAI using your personal account's RBAC permissions.

---

## Project Structure

```
hydro-one-hods-api/
├── app/
│   ├── main.py          # FastAPI app: routes, CORS, startup
│   ├── auth.py          # Entra ID JWT validation (require_auth dependency)
│   ├── search_index.py  # Azure AI Search hybrid query + result collapsing
│   ├── llm.py           # Azure OpenAI client + query optimization prompt
│   ├── models.py        # Pydantic request/response models
│   └── telemetry.py     # Azure Monitor / OpenTelemetry bootstrap
├── tests/
│   ├── __init__.py
│   └── test_health.py   # 6 unit tests (all Azure calls mocked)
├── requirements.txt
├── ruff.toml
└── .env.example
```

### Key endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | None | Liveness probe — returns `{"status": "ok"}` |
| `POST` | `/query` | Bearer JWT | Hybrid semantic search; returns documents + captions + answers |
| `POST` | `/OptimizeHybridQueries` | Bearer JWT | GPT-4o query rewriting; returns `OptimizedQuery` + `keywords` |

### Key modules

| Module | Purpose |
|---|---|
| `auth.py` | Fetches JWKS from Entra ID, validates RS256 JWT (audience, issuer, exp) |
| `search_index.py` | Builds OData filter, runs `VectorizableTextQuery` + semantic reranking, collapses chunks to documents |
| `llm.py` | `DefaultAzureCredential`-backed `AzureOpenAI` client; GPT-4o structured JSON output |

---

## Running Tests

All tests mock Azure dependencies — no credentials or live services needed.

```bash
cd hydro-one-hods-api
pytest tests/ -v
```

```bash
# With coverage
pytest tests/ -v --cov=app --cov-report=term-missing
```

### What is tested

| Test | Covers |
|---|---|
| `test_health` | `GET /health` returns 200 with `{"status":"ok"}` |
| `test_query_requires_auth` | `POST /query` without token returns 403 |
| `test_optimize_requires_auth` | `POST /OptimizeHybridQueries` without token returns 403 |
| `test_filter_builder` | OData filter with Prefix (scalar) and ContentType (collection any()) |
| `test_filter_builder_empty` | Empty filter list returns `None` |
| `test_filter_odata_injection` | Single quotes in values are doubled (OData escape) |

### What is NOT yet tested (gaps)

- Valid JWT token flow — requires mocking `_get_jwks()` to return a test RSA key pair
- `run_search()` output shape — requires mocking `SearchClient.search()`
- `/OptimizeHybridQueries` success path — requires mocking `AzureOpenAI.chat.completions.create()`
- JWKS TTL expiry and kid-not-found retry (ISSUE-6)
- Pagination parameters (ISSUE-12)

Example mock for the valid JWT test:

```python
from unittest.mock import patch
from jose import jwt
import time

def make_test_token(tenant_id, client_id, private_key_pem):
    return jwt.encode(
        {"aud": client_id, "iss": f"https://sts.windows.net/{tenant_id}/", "exp": time.time() + 3600},
        private_key_pem,
        algorithm="RS256",
    )

def test_query_with_valid_token(client):
    token = make_test_token(...)
    with patch("app.auth._get_jwks", return_value={"keys": [<test-public-jwk>]}):
        resp = client.post("/query", json={"query": "test"}, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
```

---

## Linting

```bash
ruff check app/ tests/
ruff format app/ tests/
```

`ruff.toml` is already present with `target-version = "py312"` and `line-length = 100`.

---

## Deployment

Deployed via `azure-pipelines/api.yml`. Manual deployment:

```bash
az webapp deployment source config-zip \
  --resource-group <rg-name> \
  --name <api-app-name> \
  --src app.zip
```

The startup command is set in Bicep:

```
pip install -r requirements.txt && uvicorn app.main:app --host 0.0.0.0 --port 8000
```

After deployment, hit `GET https://<api-app-url>/health` — expect `{"status": "ok"}`.
