from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.rstrip("/")


@dataclass(frozen=True)
class Settings:
    azure_openai_endpoint: str
    azure_openai_api_key: str
    embedding_deployment: str
    generation_deployment_primary: str
    generation_deployment_comparison: str
    azure_search_endpoint: str
    azure_search_api_key: str
    azure_search_index_name: str
    api_key_auth_secret: str
    database_path: str
    retrieval_min_score: float = 0.028
    semantic_ranking_enabled: bool = False


def get_settings() -> Settings:
    database_url = os.getenv("DATABASE_URL", "sqlite:///./meridian.db")
    database_path = database_url.removeprefix("sqlite:///")
    return Settings(
        azure_openai_endpoint=required_env("AZURE_OPENAI_ENDPOINT"),
        azure_openai_api_key=required_env("AZURE_OPENAI_API_KEY"),
        embedding_deployment=required_env("EMBEDDING_DEPLOYMENT"),
        generation_deployment_primary=required_env("GENERATION_DEPLOYMENT_PRIMARY"),
        generation_deployment_comparison=os.getenv("GENERATION_DEPLOYMENT_COMPARISON", "DeepSeek-V3.2"),
        azure_search_endpoint=required_env("AZURE_SEARCH_ENDPOINT"),
        azure_search_api_key=required_env("AZURE_SEARCH_API_KEY"),
        azure_search_index_name=os.getenv("AZURE_SEARCH_INDEX_NAME", "meridian-knowledge-base"),
        api_key_auth_secret=required_env("API_KEY_AUTH_SECRET"),
        database_path=database_path,
        retrieval_min_score=float(os.getenv("RETRIEVAL_MIN_SCORE", "0.028")),
        semantic_ranking_enabled=os.getenv("SEMANTIC_RANKING_ENABLED", "false").lower() in {"1", "true", "yes"},
    )
