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
    # 图的最大步数。一次工具轮 = agent + tools 两步，所以 40 约等于 18 轮，
    # 比 max_tool_rounds 宽松，真正先触发的是业务预算而不是框架报错。
    recursion_limit: int = 40
    # 单轮对话里允许的工具轮数预算：到这里强制收尾，用已有证据作答
    max_tool_rounds: int = 6
    # 单条 AIMessage 里允许并行发起的工具调用数，多余的丢弃
    max_calls_per_round: int = 6


@lru_cache
def get_settings() -> Settings:
    return Settings()
