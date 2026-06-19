# Hydro One HODS API

A FastAPI service that exposes:

- `POST /query` — run a hybrid (keyword + vector) semantic query against an
  Azure AI Search index, with semantic reranking, captions, and answers.
- `POST /OptimizeHybridQueries` — clean up a natural-language query (spelling /
  word-spacing fixes) and derive keywords for hybrid search, powered by an
  Azure OpenAI model.

## Requirements

- Python 3.12+
- An Azure OpenAI / Foundry resource with a chat model deployment
- An Azure AI Search service with a semantic-enabled index
- An Entra identity with these roles (auth uses RBAC / Entra ID — no API keys):
  - **Cognitive Services OpenAI User** on the Azure OpenAI resource
  - **Search Index Data Reader** on the Azure AI Search service (querying)
  - **Search Service Contributor** on the Azure AI Search service (only needed
    to run the index admin script below)

## Setup

Create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configuration

Configuration is read from environment variables (loaded from a `.env` file via
`python-dotenv`). Copy `.env.example` to `.env` and set your values:

```powershell
Copy-Item .env.example .env
```

| Variable                        | Description                                            |
| ------------------------------- | ------------------------------------------------------ |
| `AZURE_OPENAI_BASE_URL`         | Foundry v1 endpoint, e.g. `https://<res>.services.ai.azure.com/openai/v1/`. |
| `AZURE_OPENAI_API_VERSION`      | API version, e.g. `preview`.                           |
| `AZURE_OPENAI_DEPLOYMENT_NAME`  | Chat model deployment name, e.g. `gpt-5.4-nano`.       |
| `AZURE_OPENAI_TOKEN_SCOPE`      | Optional. Defaults to `https://cognitiveservices.azure.com/.default`. |
| `AZURE_SEARCH_ENDPOINT`         | AI Search service endpoint, e.g. `https://<svc>.search.windows.net`. |
| `AZURE_SEARCH_INDEX_NAME`       | Search index name, e.g. `rag-1781805151291`.           |
| `AZURE_SEARCH_SEMANTIC_CONFIGURATION` | Semantic configuration name on the index.        |
| `AZURE_SEARCH_TOKEN_SCOPE`      | Optional. Defaults to `https://search.azure.com/.default`. |

`.env` is gitignored and must not be committed.

Authentication uses `DefaultAzureCredential`, so locally sign in with
`az login` (or use a managed identity when hosted). A fresh Entra bearer token
is sent with every model call.

## Run

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

The API starts at `http://127.0.0.1:8000`.

Interactive API docs (Swagger UI) are available at `http://127.0.0.1:8000/docs`.

## Endpoints

| Method | Path                     | Description                                                  |
| ------ | ------------------------ | ----------------------------------------------------------- |
| GET    | `/health`                | Health check. Returns `{"status": "ok"}`.                   |
| POST   | `/query`                 | Submit a query with keywords and filters.                   |
| POST   | `/OptimizeHybridQueries` | Clean a natural-language query and derive keywords via LLM. |

## `POST /query`

### Request body

| Field      | Type                  | Description                                |
| ---------- | --------------------- | ------------------------------------------ |
| `query`    | `string`              | The free-text query.                       |
| `keywords` | `string[]`            | List of keywords to refine the query.      |
| `filter`   | `KeyValuePair[]`      | List of `{ "key": ..., "value": ... }`.    |

Each `KeyValuePair` has:

| Field   | Type     | Description        |
| ------- | -------- | ------------------ |
| `key`   | `string` | The filter name.   |
| `value` | `string` | The filter value.  |

Filters are translated into an OData `$filter`. Known filterable fields
(`Prefix`, `ContentType`, `parent_id`) are matched case-insensitively, so
`prefix` and `contentType` resolve to the index's actual field names.
`ContentType` is a collection field, so it is filtered with an `any()` lambda
(`ContentType/any(c: c eq 'value')`); scalar fields use `eq`.

> Filterable fields must be marked `filterable` on the index. Use
> `scripts/set_fields_filterable.py` to (re)build the index with `Prefix` and
> `ContentType` filterable — see [Index administration](#index-administration).

### Sample request

```json
{
  "query": "tree falling near building near multiple transformers",
  "keywords": ["tree", "falling"],
  "filter": [
    {
      "key": "ContentType",
      "value": "[\"contenttype1\",\"contenttype2\"]"
    },
    {
      "key": "prefix",
      "value": "AL"
    }
  ]
}
```

### Example: curl

```bash
curl -X POST "http://127.0.0.1:8000/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "tree falling near building near multiple transformers",
    "keywords": ["tree", "falling"],
    "filter": [
      { "key": "ContentType", "value": "[\"contenttype1\",\"contenttype2\"]" },
      { "key": "prefix", "value": "AL" }
    ]
  }'
```

### Example: PowerShell

```powershell
$body = @{
    query    = "tree falling near building near multiple transformers"
    keywords = @("tree", "falling")
    filter   = @(
        @{ key = "ContentType"; value = '["contenttype1","contenttype2"]' }
        @{ key = "prefix";      value = "AL" }
    )
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "http://127.0.0.1:8000/query" -Method Post `
    -ContentType "application/json" -Body $body
```

### Sample response

The endpoint runs the hybrid semantic query and returns the search results.
The same JSON is also printed to the server console for debugging.

```json
{
  "count": 42,
  "answers": [
    {
      "key": "chunk_id-123",
      "text": "A fallen tree near a building can damage nearby transformers...",
      "highlights": "A fallen <em>tree</em> near a building can damage nearby <em>transformers</em>...",
      "score": 0.92
    }
  ],
  "results": [
    {
      "chunk": "...matching passage text...",
      "title": "Vegetation Management Manual",
      "Prefix": "AL",
      "ContentType": ["contenttype1"],
      "score": 12.34,
      "reranker_score": 2.71,
      "captions": [
        {
          "text": "...extractive caption...",
          "highlights": "...extractive <em>caption</em>..."
        }
      ]
    }
  ]
}
```

## `POST /OptimizeHybridQueries`

Sends the input text to an Azure OpenAI model that fixes spelling and
word-spacing errors (e.g. `"ahydro plant"` -> `"a hydro plant"`) and extracts
search keywords.

### Request body

| Field  | Type     | Description                          |
| ------ | -------- | ------------------------------------ |
| `text` | `string` | The natural-language query to clean. |

### Sample request

```json
{
  "text": "ahydro plant near multiple transfomers"
}
```

### Example: curl

```bash
curl -X POST "http://127.0.0.1:8000/OptimizeHybridQueries" \
  -H "Content-Type: application/json" \
  -d '{ "text": "ahydro plant near multiple transfomers" }'
```

### Example: PowerShell

```powershell
$body = @{ text = "ahydro plant near multiple transfomers" } | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:8000/OptimizeHybridQueries" -Method Post `
    -ContentType "application/json" -Body $body
```

### Sample response

```json
{
  "OptimizedQuery": "a hydro plant near multiple transformers",
  "keywords": ["hydro", "plant", "multiple", "transformers"]
}
```

## Index administration

In Azure AI Search, the `filterable` attribute is immutable on existing fields —
it cannot be toggled in place. To make `Prefix` and `ContentType` filterable,
`scripts/set_fields_filterable.py` reads the live index definition, sets
`filterable=True` on those fields, then **deletes and recreates** the index.

> **Recreating the index deletes all documents.** Re-run your indexer (or
> re-upload documents) afterwards to repopulate it.

```powershell
# Dry run — shows what would change
.\.venv\Scripts\python.exe scripts\set_fields_filterable.py

# Apply — deletes and recreates the index
.\.venv\Scripts\python.exe scripts\set_fields_filterable.py --yes
```

## Project structure

```
app/
  __init__.py        Package marker
  main.py            FastAPI app and route definitions
  models.py          Pydantic request/response models
  llm.py             Azure OpenAI client (Entra auth) and system prompt
  search_index.py    Azure AI Search client and hybrid semantic query
scripts/
  set_fields_filterable.py  Admin: recreate index with filterable fields
.env.example         Template for required environment variables
requirements.txt
```
