"""Entra ID (Azure AD) bearer-token validation for the HODS API.

Every protected endpoint calls ``require_auth`` as a FastAPI dependency.
The token must be issued by the configured tenant and target the correct
audience (the API's own app-registration client ID).

Environment variables
---------------------
AZURE_TENANT_ID   – Entra tenant ID (required)
AZURE_CLIENT_ID   – This API's app-registration client ID (required)
"""

import os
from typing import Annotated

import requests
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "")
ISSUER    = f"https://sts.windows.net/{TENANT_ID}/"
JWKS_URL  = f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"

_bearer = HTTPBearer()

# Simple in-process JWKS cache — keys rotate rarely.
_jwks_cache: dict | None = None


def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache is None:
        resp = requests.get(JWKS_URL, timeout=10)
        resp.raise_for_status()
        _jwks_cache = resp.json()
    return _jwks_cache


def _credentials_error(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_auth(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> dict:
    """FastAPI dependency — validates the Entra ID bearer token.

    Returns the decoded JWT claims on success.
    Raises HTTP 401 on any validation failure.
    """
    if not TENANT_ID or not CLIENT_ID:
        raise _credentials_error("API authentication is not configured.")

    token = credentials.credentials
    try:
        jwks = _get_jwks()
        header = jwt.get_unverified_header(token)
        key = next(
            (k for k in jwks["keys"] if k["kid"] == header.get("kid")), None
        )
        if key is None:
            raise _credentials_error("Token signing key not found.")

        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=CLIENT_ID,
            issuer=ISSUER,
            options={"verify_exp": True},
        )
        return claims
    except JWTError as exc:
        raise _credentials_error(f"Token validation failed: {exc}") from exc
