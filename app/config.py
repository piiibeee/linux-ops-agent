"""全局配置。所有可调参数集中在此，通过 .env 覆盖。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── 大模型 ──────────────────────────────────────────────
    dashscope_api_key: str = ""
    llm_model: str = "qwen-plus"
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_temperature: float = 0.0

    # ── 向量检索 ────────────────────────────────────────────
    embed_model: str = "text-embedding-v3"
    chroma_dir: str = "./data/chroma"
    collection: str = "ops_runbook"
    retrieval_top_k: int = 4

    # ── 会话存储 ────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    history_rounds: int = 6          # 保留最近 N 轮对话进上下文
    session_ttl: int = 60 * 60 * 6   # 6 小时过期

    # ── 命令执行安全 ────────────────────────────────────────
    allow_execute: bool = True       # 演练环境可设为 false，工具只返回提示不真执行
    cmd_timeout: int = 10
    max_output_chars: int = 4000     # 单次命令输出截断，防止撑爆上下文

    # ── Agent ──────────────────────────────────────────────
    recursion_limit: int = 16        # 图的最大步数，防止工具循环


@lru_cache
def get_settings() -> Settings:
    return Settings()
