# Hydro One HODS UI

A React + Vite single-page UI for searching the Hydro One HODS document index. It
calls the downstream **HODS API** for hybrid search and uses an LLM-backed query
optimizer to clean up search terms and derive keywords before searching.

## Features

- **Search box** — free-text query that populates the `query` sent to the API.
- **Automatic query optimization** — 1.1 s after you stop typing, the app calls
  `/OptimizeHybridQueries`, writes the cleaned query back into the search box, and
  fills the **keywords** structure (shown as bubbles below the box).
  - Runs asynchronously; the **last call always wins** (stale/superseded responses
    are ignored and older in-flight calls are aborted).
  - Never overwrites the box/keywords if you keep typing.
  - Any single call is killed if it takes longer than **5 seconds**.
  - A spinner + animated dots appear while optimizing.
- **Content Type filter** — checkbox **multi-select** dropdown. Selected values show
  as removable bubbles below the dropdown and are sent as one filter pair each.
- **Prefix filter** — single-select dropdown (`AL`, `BU`, `FP`, `HO`, `PR`, `SP`).
- **Results** — rendered from the API response:
  - **Top Answers** — semantic answers with highlighted snippets.
  - Per result: **title**, **Prefix** and **ContentType** badges, a **Matched
    Result** caption with `<em>` highlighting, and a collapsible **Document
    Excerpt** (the full chunk).

## API integration

The UI talks to two endpoints (see the OpenAPI spec for full schemas):

### `POST /OptimizeHybridQueries`

Request:

```json
{ "text": "saftey gloves" }
```

Response:

```json
{ "OptimizedQuery": "safety gloves", "keywords": ["safety", "gloves"] }
```

### `POST /query`

Request body (`QueryRequest`):

```json
{
  "query": "safety gloves",
  "keywords": ["safety", "gloves"],
  "filter": [
    { "key": "contentType", "value": "Bulletins" },
    { "key": "prefix", "value": "AL" }
  ]
}
```

- The search box populates `query`.
- The optimizer populates `keywords`.
- The two dropdowns populate `filter` (one `KeyValuePair` per selected content
  type, plus one for the prefix when set).

Response (consumed shape):

```json
{
  "count": 6,
  "answers": [
    { "key": "...", "text": "...", "highlights": "... <em>...</em> ...", "score": 0.95 }
  ],
  "results": [
    {
      "chunk": "full document text...",
      "title": "Document.pdf",
      "Prefix": "AL",
      "ContentType": ["General", "Bulletins"],
      "score": 0.033,
      "reranker_score": 2.74,
      "captions": [
        { "text": "...", "highlights": "... <em>...</em> ..." }
      ]
    }
  ]
}
```

## Configuration

Environment variables (e.g. in `.env.local`):

| Variable           | Default                 | Purpose                                                        |
| ------------------ | ----------------------- | -------------------------------------------------------------- |
| `VITE_API_TARGET`  | `http://localhost:8000` | Backend the Vite **dev-server proxy** forwards API calls to.   |
| `VITE_API_BASE_URL`| `` (same origin)        | Base URL prefixed to API requests from the browser.            |

During development, requests to `/query`, `/OptimizeHybridQueries`, and `/health`
are proxied to `VITE_API_TARGET` (configured in `vite.config.js`), so the browser
calls stay same-origin and avoid CORS.

## Getting started

```bash
npm install
npm run dev      # start the dev server (default http://localhost:5173/)
```

Make sure the HODS API is running and reachable at `VITE_API_TARGET`
(default `http://localhost:8000`).

### Scripts

| Script            | Description                     |
| ----------------- | ------------------------------- |
| `npm run dev`     | Start the Vite dev server.      |
| `npm run build`   | Production build into `dist/`.  |
| `npm run preview` | Preview the production build.   |

## Project structure

```
index.html          App entry HTML
vite.config.js      Vite config + dev API proxy
src/
  main.jsx          React entry
  App.jsx           Search UI, filters, query optimization, results rendering
  index.css         Styles
```
