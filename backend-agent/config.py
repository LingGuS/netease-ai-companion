"""全局配置：集中在 .env 中管理，避免各模块散落硬编码。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 固定在项目根目录（backend-agent 的上一级）；无论从哪个目录启动都能找到
_PROJECT_ROOT_ENV = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[_PROJECT_ROOT_ENV, ".env"], env_file_encoding="utf-8", extra="ignore"
    )

    # ---- LLM（OpenAI 兼容接口）----
    llm_api_base: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-flash"
    llm_temperature: float = 0.7

    # ---- 服务地址 ----
    music_api_base_url: str = "http://127.0.0.1:3000"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000

    # ---- 数据层 ----
    database_url: str = "postgresql://postgres:postgres@127.0.0.1:5432/netease_ai"
    milvus_db_path: str = "data/milvus.db"  # 相对项目根目录
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dim: int = 512


@lru_cache
def get_settings() -> Settings:
    return Settings()
