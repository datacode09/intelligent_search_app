# UI Refactor — Document-Centric Results (Pagination Removed)

The `/query` API now returns a **deduped, document-centric** result set instead of
paged chunk results. One object is returned per source document (chunks sharing a
`parent_id` are collapsed to the highest-ranked one). Pagination has been removed.

## API contract

### Request — `POST /query`

```json
{
  "query": "transformer maintenance",
  "keywords": [],
  "filter": []
}
```

- `query` — the (optionally optimized) search text.
- `keywords` — optional derived keywords; combined into the keyword leg.
- `filter` — optional `[{ "key": "...", "value": "..." }]` pairs.
- **No `top` / `skip`.** The API caps results internally (top 100 chunks before
  collapsing); the search is restrictive enough that real queries return far fewer.

### Response

```json
{
  "count": 7,
  "answers": [ ... ],
  "results": [
    {
      "DocumentName": "Electrical Safety Gloves Standard.pdf",
      "DocumentUrl": "#",
      "Prefix": "Safety",
      "ContentType": ["Standard"],
      "Highlights": ["All <em>gloves</em> must be inspected prior to use..."]
    }
  ]
}
```

| Field          | Type       | Notes                                                        |
| -------------- | ---------- | ------------------------------------------------------------ |
| `count`        | number     | Number of **documents** returned (not a total match count).  |
| `DocumentName` | string     | Display title of the document.                               |
| `DocumentUrl`  | string     | **Stubbed as `"#"`** until the index exposes a path field.   |
| `Prefix`       | string     | Single category/prefix value.                                |
| `ContentType`  | string[]   | Collection field — always render as a list.                  |
| `Highlights`   | string[]   | Caption snippets containing `<em>` tags from the reranker.   |
| `answers`      | object[]   | Optional extractive answers (passage-level). May be ignored. |

## 1. Remove pagination

Delete:

- `page`, `setPage`, `PAGE_SIZE`, `totalPages` state.
- The `<Pagination>` component and its render.
- `top` / `skip` from the request body.
- "Reset to page 1 on new search" logic and the `page` dependency in the search effect.

## 2. Simplify the fetch call

```ts
async function runSearch(query, keywords, filter) {
  setLoading(true);
  try {
    const res = await fetch("/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, keywords, filter }),
    });
    if (!res.ok) throw new Error(`Search failed: ${res.status}`);

    const data = await res.json();
    setResults(data.results ?? []);
    setCount(data.count ?? 0); // count = number of documents
  } finally {
    setLoading(false);
  }
}
```

Trigger only on submit / filter change:

```ts
useEffect(() => {
  runSearch(query, keywords, filter);
}, [query, keywords, filter]);
```

## 3. Render document results

```tsx
<p>Showing {count} document{count === 1 ? "" : "s"}</p>

{results.map((doc, i) => (
  <article key={doc.DocumentName ?? i} className="result-card">
    <h3>
      {doc.DocumentUrl && doc.DocumentUrl !== "#" ? (
        <a href={doc.DocumentUrl} target="_blank" rel="noopener noreferrer">
          {doc.DocumentName}
        </a>
      ) : (
        <span>{doc.DocumentName}</span>
      )}
    </h3>

    <div className="result-meta">
      {doc.Prefix && <span className="badge">{doc.Prefix}</span>}
      {(doc.ContentType ?? []).map((ct) => (
        <span key={ct} className="badge">{ct}</span>
      ))}
    </div>

    <ul className="highlights">
      {(doc.Highlights ?? []).map((h, j) => (
        <li key={j} dangerouslySetInnerHTML={{ __html: sanitize(h) }} />
      ))}
    </ul>
  </article>
))}
```

## 4. Security — sanitize highlights before injecting HTML

`Highlights` contain `<em>` tags from the search service, so rendering uses
`dangerouslySetInnerHTML`. **Never inject the raw string** — sanitize first to
prevent XSS, since the content originates from indexed documents. Use
[DOMPurify](https://github.com/cure53/DOMPurify):

```ts
import DOMPurify from "dompurify";

// Allow only the emphasis tag the reranker emits; strip everything else.
const sanitize = (html: string) =>
  DOMPurify.sanitize(html, { ALLOWED_TAGS: ["em"], ALLOWED_ATTR: [] });
```

## 5. `DocumentUrl` is a stub

Every `DocumentUrl` is currently `"#"` (the index has no URL/path field yet).
Treat `"#"` as "no link" — render the name as plain text (shown in section 3).
When the index later exposes a real path, the link lights up automatically.

## Checklist

- [ ] Remove `page` / `PAGE_SIZE` / `totalPages` state and the `<Pagination>` UI.
- [ ] Drop `top` / `skip` from the request body.
- [ ] Map `DocumentName` / `DocumentUrl` / `Prefix` / `ContentType` / `Highlights`.
- [ ] Render `ContentType` as a list (collection field).
- [ ] Sanitize `Highlights` with DOMPurify before `dangerouslySetInnerHTML`.
- [ ] Treat `DocumentUrl === "#"` as "no link yet".
- [ ] Use `count` as a simple total, not for page math.
