"""Azure OpenAI client and prompt for keyword derivation."""

import os

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Foundry v1 endpoint and deployment (from environment variables).
BASE_URL = os.environ["AZURE_OPENAI_BASE_URL"]
API_VERSION = os.environ["AZURE_OPENAI_API_VERSION"]
DEPLOYMENT_NAME = os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]

# Data-plane scope for Azure OpenAI / Cognitive Services inference.
TOKEN_SCOPE = os.environ.get(
    "AZURE_OPENAI_TOKEN_SCOPE", "https://cognitiveservices.azure.com/.default"
)

# System prompt instructing the model to clean the query and return keywords as JSON.
SYSTEM_PROMPT = """\
# Role
Optimize a natural-language search query for hybrid search.

# Input
A single natural-language string.

# Tasks
1. Fix spelling mistakes and word-spacing errors (e.g. "ahydro plant" -> \
"a hydro plant", "transfomer" -> "transformer"). Preserve the original meaning. \
Do not add or remove concepts.
2. Extract the salient keywords from the corrected text (nouns, entities, \
distinctive terms). Lowercase. Drop stopwords, filler, and punctuation. \
Deduplicate, preserve input order.

# Output
JSON only. No prose, no code fences:
{"OptimizedQuery": "string", "keywords": ["string", ...]}

# Example
Input: "ahydro plant near multiple transfomers"
Output: {"OptimizedQuery": "a hydro plant near multiple transformers", \
"keywords": ["hydro", "plant", "multiple", "transformers"]}
"""

# RBAC / Entra ID auth — the provider returns a bearer token (cached and
# refreshed by DefaultAzureCredential). No API key is used.
token_provider = get_bearer_token_provider(DefaultAzureCredential(), TOKEN_SCOPE)

# Plain OpenAI client targeting the Azure Foundry v1 endpoint. The v1 surface
# is selected via the api-version query param. A placeholder api_key is set at
# construction; the real Entra bearer token is injected per request by
# get_client() so it stays fresh.
_base_client = OpenAI(
    base_url=BASE_URL,
    api_key="placeholder-overridden-per-request",
    default_query={"api-version": API_VERSION},
)


def get_client() -> OpenAI:
    """Return a client carrying a fresh Entra bearer token (sent as Bearer)."""
    return _base_client.with_options(api_key=token_provider())


