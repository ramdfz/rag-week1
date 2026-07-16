from __future__ import annotations

import os

import requests
from dotenv import load_dotenv


API_VERSION = "2024-07-01"
CONFIG_NAME = "meridian-semantic-config"


def main() -> None:
    load_dotenv()
    endpoint = os.environ["AZURE_SEARCH_ENDPOINT"].rstrip("/")
    api_key = os.environ["AZURE_SEARCH_API_KEY"]
    index_name = os.getenv("AZURE_SEARCH_INDEX_NAME", "meridian-knowledge-base")
    url = f"{endpoint}/indexes/{index_name}?api-version={API_VERSION}"
    headers = {"api-key": api_key, "Content-Type": "application/json"}

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    index = response.json()
    index["semantic"] = {
        "defaultConfiguration": CONFIG_NAME,
        "configurations": [
            {
                "name": CONFIG_NAME,
                "prioritizedFields": {
                    "titleField": {"fieldName": "section_title"},
                    "prioritizedContentFields": [{"fieldName": "text"}],
                    "prioritizedKeywordsFields": [{"fieldName": "source_document"}],
                },
            }
        ],
    }

    update = requests.put(url, headers=headers, json=index, timeout=60)
    if update.status_code >= 400:
        raise RuntimeError(f"Semantic configuration update failed: HTTP {update.status_code} {update.text}")

    print(f"Semantic configuration '{CONFIG_NAME}' is present on index '{index_name}'.")


if __name__ == "__main__":
    main()
