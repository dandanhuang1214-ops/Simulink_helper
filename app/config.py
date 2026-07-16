from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Simulink Assistant"
    app_version: str = "0.1.0"
    ollama_base_url: str = "http://ollama:11434"
    qdrant_url: str = "http://qdrant:6333"
    database_url: str = "sqlite:////app/data/app.db"
    chat_model: str = "qwen3.5:2b"
    embedding_model: str = "qwen3-embedding:0.6b"
    embedding_dimension: int = 1024
    qdrant_collection: str = "simulink_documents"
    ollama_think: bool = False
    knowledge_root: str = "/app/knowledge"
    max_upload_mb: int = 100
    worker_poll_seconds: float = 2.0
    embedding_batch_size: int = 4
    embedding_cache_enabled: bool = True
    retrieval_profile: str = "fast"
    dense_fast_path_enabled: bool = True
    retrieval_top_k: int = 20
    rerank_top_k: int = 6
    llm_rerank_enabled: bool = True
    graph_retrieval_enabled: bool = True
    graph_retrieval_top_k: int = 24
    evidence_candidate_k: int = 18
    evidence_final_k: int = 6
    judge_threshold: float = 0.65
    llm_judge_enabled: bool = False
    enable_docling_vlm: bool = False
    wiki_llm_max_chunks: int = 800

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
