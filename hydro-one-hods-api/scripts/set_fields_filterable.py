"""One-time admin script: make ``Prefix`` and ``ContentType`` filterable.

Azure AI Search treats ``filterable`` (and ``sortable`` / ``facetable`` /
``searchable``) as immutable, build-time field attributes. They cannot be
changed in place with ``create_or_update_index`` — the service rejects the
edit. The only supported path is to drop and recreate the index with the
corrected field definitions.

WARNING: recreating the index DELETES all indexed documents. After running
this script you must re-populate the index (re-run your indexer, or re-upload
documents) before queries return results again.

Usage (from the repo root, with .env configured and ``az login`` done):

    .\\.venv\\Scripts\\python.exe scripts\\set_fields_filterable.py --yes

The identity needs the **Search Service Contributor** role on the service.
"""

import argparse
import os
import sys

from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient
from dotenv import load_dotenv

load_dotenv()

# Fields that must become filterable.
FIELDS_TO_MAKE_FILTERABLE = {"Prefix", "ContentType"}

SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
SEARCH_INDEX_NAME = os.environ["AZURE_SEARCH_INDEX_NAME"]
SEARCH_AUDIENCE = "https://search.azure.com"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm that recreating the index (deleting all documents) is OK.",
    )
    args = parser.parse_args()

    client = SearchIndexClient(
        endpoint=SEARCH_ENDPOINT,
        credential=DefaultAzureCredential(),
        audience=SEARCH_AUDIENCE,
    )

    index = client.get_index(SEARCH_INDEX_NAME)

    changed: list[str] = []
    for field in index.fields:
        if field.name in FIELDS_TO_MAKE_FILTERABLE and not field.filterable:
            field.filterable = True
            changed.append(field.name)

    if not changed:
        print("Nothing to do: target fields are already filterable.")
        return 0

    print(f"Index : {SEARCH_INDEX_NAME}")
    print(f"Fields to make filterable: {', '.join(changed)}")
    print(
        "\nThis will DELETE and RECREATE the index. All indexed documents will "
        "be lost and must be re-indexed afterwards."
    )

    if not args.yes:
        print("\nRe-run with --yes to proceed.")
        return 1

    # filterable is immutable on existing fields, so recreate the index.
    client.delete_index(SEARCH_INDEX_NAME)
    client.create_index(index)

    print(
        f"\nDone. '{SEARCH_INDEX_NAME}' recreated with {', '.join(changed)} "
        "filterable. Re-run your indexer (or re-upload documents) to repopulate it."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
