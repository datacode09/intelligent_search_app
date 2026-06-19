"""HODS API - FastAPI application."""

import json
import os
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.auth import require_auth
from app.llm import DEPLOYMENT_NAME, SYSTEM_PROMPT, get_client
from app.models import (
    OptimizeHybridQueriesRequest,
    OptimizeHybridQueriesResponse,
    QueryRequest,
)
from app.search_index import run_search
from app.telemetry import configure_telemetry

configure_telemetry()

app = FastAPI(title="Hydro One HODS API", version="1.0.0")

_allowed_origins = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/query")
def query(
    request: QueryRequest,
    _claims: Annotated[dict, Depends(require_auth)],
) -> dict[str, Any]:
    return run_search(
        query=request.query,
        keywords=request.keywords,
        filters=request.filter,
    )


@app.post("/OptimizeHybridQueries", response_model=OptimizeHybridQueriesResponse)
def optimize_hybrid_queries(
    request: OptimizeHybridQueriesRequest,
    _claims: Annotated[dict, Depends(require_auth)],
) -> OptimizeHybridQueriesResponse:
    response = get_client().chat.completions.create(
        model=DEPLOYMENT_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": request.text},
        ],
        response_format={"type": "json_object"},
    )

    raw = (response.choices[0].message.content or "").strip()

    try:
        data = json.loads(raw)
        optimized_query = data["OptimizedQuery"]
        keywords = data["keywords"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Model did not return valid optimized-query JSON.",
        ) from exc

    return OptimizeHybridQueriesResponse(
        OptimizedQuery=optimized_query,
        keywords=keywords,
    )
