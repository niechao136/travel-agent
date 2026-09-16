from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    amap_mcp_url: str = "https://mcp.amap.com/mcp"
    public_base_url: str = "http://127.0.0.1:8000"
    checkpoint_db_path: str = "./data/checkpoints.db"
    auth_db_path: str = "./data/tokens.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
