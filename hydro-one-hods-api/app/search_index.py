"""Azure AI Search client and hybrid semantic query against the HODS index.

Translates the portal "Hybrid Query with Semantic Reranking" payload into the
``azure-search-documents`` SDK equivalents:

    REST field             -> SDK argument
    --------------------------------------------------------------
    search                 -> search_text
    count                  -> include_total_count=True
    vectorQueries[kind=text]-> VectorizableTextQuery (server-side vectorizer)
    queryType=semantic     -> query_type=QueryType.SEMANTIC
    semanticConfiguration  -> semantic_configuration_name
    captions=extractive    -> query_caption=QueryCaptionType.EXTRACTIVE
    answers=extractive|count-3 -> query_answer + query_answer_count
    queryLanguage=en-us    -> query_language=QueryLanguage.EN_US
    queryRewrites=generative-> query_rewrites="generative"
    select                 -> select=[...]
"""

import os
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import (
    QueryLanguage,
    QueryType,
    VectorizableTextQuery,
)
from dotenv import load_dotenv

load_dotenv()

# Search service configuration (index name externalized to .env as requested).
SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
SEARCH_INDEX_NAME = os.environ["AZURE_SEARCH_INDEX_NAME"]
SEMANTIC_CONFIG_NAME = os.environ["AZURE_SEARCH_SEMANTIC_CONFIGURATION"]

# Data-plane scope for Azure AI Search (RBAC / Entra ID — no API key).
SEARCH_TOKEN_SCOPE = os.environ.get(
    "AZURE_SEARCH_TOKEN_SCOPE", "https://search.azure.com/.default"
)

# Fields needed to build the document-centric result set. ``parent_id`` is the
# dedup key: one source document is chunked into many rows that share a
# parent_id, and we collapse those back down to a single document per parent.
#
# TODO [ISSUE-4 CRITICAL]: DocumentUrl is hardcoded as "#" — all document links in the UI are broken.
# The SharePoint webUrl must be stored as blob metadata during ingest and mapped through the index.
#
# Fix — 3 steps:
#
# Step 1 (ingest/function_app.py): capture the SharePoint item's webUrl as blob metadata:
#   metadata["SourceUrl"] = _to_blob_metadata_value(item.get("webUrl", ""))
#
# Step 2 (infra/search-index.json): add a new field to the index:
#   { "name": "SourceUrl", "type": "Edm.String", "searchable": false, "retrievable": true }
#
# Step 3 (infra/search-indexer.json): add a field mapping from blob metadata to the index field:
#   { "sourceFieldName": "metadata_SourceUrl", "targetFieldName": "SourceUrl" }
#
# Then add "SourceUrl" here so it is returned in results:
#   SELECT_FIELDS = ["title", "Prefix", "ContentType", "parent_id", "SourceUrl"]
#
# And in _collapse_to_documents replace the stub:
#   "DocumentUrl": result.get("SourceUrl") or "#",
#
# To test: upload a single file to Blob Storage with metadata["SourceUrl"] set,
# re-run the indexer, then call GET /query and assert DocumentUrl != "#".
#
# TODO [ISSUE-11 MEDIUM]: "content" (raw chunk text) is not included in SELECT_FIELDS.
# Not needed for display (captions cover it) but useful for downstream integrations.
# Add "content" to SELECT_FIELDS if consumers need the raw text.
SELECT_FIELDS = ["title", "Prefix", "ContentType", "parent_id"]

# Vector field on the index that the azureOpenAI vectorizer populates.
VECTOR_FIELD = "text_vector"

# Filterable fields on the index, keyed by lowercased name for case-insensitive
# matching. Value is (canonical field name, is_collection). Collection fields
# (Collection(Edm.String)) must be filtered with an any()/all() lambda, not eq.
FILTERABLE_FIELDS: dict[str, tuple[str, bool]] = {
    "prefix": ("Prefix", False),
    "contenttype": ("ContentType", True),
    "parent_id": ("parent_id", False),
}

# Number of nearest neighbors to retrieve for the vector leg of the hybrid query.
DEFAULT_K_NEAREST_NEIGHBORS = 50

# Hard cap on chunks pulled back per query, purely a performance guard. We
# collapse these to documents afterwards; the search is restrictive enough that
# real queries return far fewer, and the semantic reranker only reranks ~50.
DOCUMENT_RESULT_LIMIT = 100

# Number of extractive answers to request (REST: answers="extractive|count-3").
ANSWER_COUNT = 3

_credential = DefaultAzureCredential()
_client = SearchClient(
    endpoint=SEARCH_ENDPOINT,
    index_name=SEARCH_INDEX_NAME,
    credential=_credential,
    audience="https://search.azure.com",
)


def get_client() -> SearchClient:
    """Return the shared Azure AI Search client."""
    return _client


def _build_filter(filters: list[Any]) -> str | None:
    """Build an OData ``$filter`` from a list of key/value pairs.

    Known filterable fields are canonicalized case-insensitively. Collection
    fields (e.g. ``ContentType``) use an ``any()`` lambda; scalar fields use
    ``eq``. Pairs are AND-ed together and single quotes are escaped per OData
    rules. Returns ``None`` when there are no filters.
    """
    clauses: list[str] = []
    for pair in filters:
        field, is_collection = FILTERABLE_FIELDS.get(
            pair.key.lower(), (pair.key, False)
        )
        value = pair.value.replace("'", "''")
        if is_collection:
            clauses.append(f"{field}/any(c: c eq '{value}')")
        else:
            clauses.append(f"{field} eq '{value}'")
    return " and ".join(clauses) if clauses else None


def run_search(
    query: str,
    keywords: list[str] | None = None,
    filters: list[Any] | None = None,
) -> dict[str, Any]:
    """Run a hybrid (keyword + vector) semantic query against the index.

    Parameters come from the ``/query`` endpoint. Chunk-level hits are collapsed
    into a document-centric result set (deduped on ``parent_id``) before being
    returned to the calling UI. At most ``DOCUMENT_RESULT_LIMIT`` chunks are
    pulled back; the restrictive search keeps real result counts well below it.
    """
    keywords = keywords or []
    filters = filters or []

    # BM25 (keyword) leg: combine the query with any derived keywords.
    search_text = " ".join([query, *keywords]).strip() or "*"

    # Vector leg: server-side vectorization of the natural-language query via
    # the index's azureOpenAI vectorizer (REST vectorQueries[kind="text"]).
    vector_query = VectorizableTextQuery(
        text=query or "*",
        k_nearest_neighbors=DEFAULT_K_NEAREST_NEIGHBORS,
        fields=VECTOR_FIELD,
    )

    # TODO [ISSUE-12 MEDIUM]: No pagination — top=100 chunks, all returned in one response.
    # For large corpora this causes slow responses and the UI renders everything at once.
    # Fix — add skip/top parameters to run_search and the /query endpoint model:
    #
    #   def run_search(query, keywords=None, filters=None, page: int = 0, page_size: int = 10):
    #       ...
    #       results = get_client().search(..., top=page_size, skip=page * page_size)
    #
    # Add to QueryRequest model (models.py):
    #   page: int = 0
    #   page_size: int = 10
    #
    # To test: call POST /query with {"query": "test", "page": 0, "page_size": 3} and
    # assert the response contains at most 3 results.
    results = get_client().search(
        search_text=search_text,
        vector_queries=[vector_query],
        query_type=QueryType.SEMANTIC,
        semantic_configuration_name=SEMANTIC_CONFIG_NAME,
        # Plain strings (not the QueryCaptionType/QueryAnswerType enums): the SDK
        # builds the answers string with an f-string, and on Python 3.12 a str
        # enum formats as "queryanswertype.extractive", which the service rejects.
        # highlight-true is spelled out (instead of query_caption_highlight_enabled=True)
        # because that flag serializes the bool as "highlight-True" (capitalized).
        query_caption="extractive|highlight-true",
        query_answer="extractive",
        query_answer_count=ANSWER_COUNT,
        query_language=QueryLanguage.EN_US,
        query_rewrites="generative",
        filter=_build_filter(filters),
        select=SELECT_FIELDS,
        top=DOCUMENT_RESULT_LIMIT,
    )

    # Iterate (and collapse) before reading answers so the first page is fetched.
    documents = _collapse_to_documents(results)

    answers = [
        {
            "key": answer.key,
            "text": answer.text,
            "highlights": answer.highlights,
            "score": answer.score,
        }
        for answer in (results.get_answers() or [])
    ]

    return {
        "count": len(documents),
        "answers": answers,
        "results": documents,
    }


def _collapse_to_documents(results: Any) -> list[dict[str, Any]]:
    """Collapse chunk-level hits into a deduped, document-centric result set.

    The index is chunked: one source document fans out into many chunks that
    share a ``parent_id``. Results arrive ranked, so we keep the first (highest
    ranked) chunk per ``parent_id`` and drop the rest, yielding one object per
    document. ``DocumentUrl`` is not yet available on the index and is stubbed
    as ``"#"`` for now.
    """
    seen: set[str] = set()
    documents: list[dict[str, Any]] = []
    for result in results:
        key = result.get("parent_id") or result.get("title")
        if key in seen:
            continue
        seen.add(key)

        highlights = [
            caption.highlights or caption.text
            for caption in (result.get("@search.captions") or [])
            if (caption.highlights or caption.text)
        ]

        documents.append(
            {
                "DocumentName": result.get("title"),
                "DocumentUrl": "#",
                "Prefix": result.get("Prefix"),
                "ContentType": result.get("ContentType"),
                "Highlights": highlights,
            }
        )
    return documents
