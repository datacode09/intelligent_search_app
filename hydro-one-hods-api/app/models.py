"""Pydantic models for the HODS API."""

from pydantic import BaseModel, Field


class KeyValuePair(BaseModel):
    """A single key/value filter pair."""

    key: str
    value: str


class QueryRequest(BaseModel):
    """Request body for the /query endpoint."""

    query: str
    keywords: list[str] = Field(default_factory=list)
    filter: list[KeyValuePair] = Field(default_factory=list)


class QueryResponse(BaseModel):
    """Response body for the /query endpoint."""

    query: str
    keywords: list[str]
    filter: list[KeyValuePair]


class OptimizeHybridQueriesRequest(BaseModel):
    """Request body for the /OptimizeHybridQueries endpoint."""

    text: str


class OptimizeHybridQueriesResponse(BaseModel):
    """Response body for the /OptimizeHybridQueries endpoint."""

    OptimizedQuery: str
    keywords: list[str]
