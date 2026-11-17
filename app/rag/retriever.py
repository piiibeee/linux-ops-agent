"""RAG 检索层：运维知识库的向量化与检索。

链路：Markdown 按标题切分 → 递归字符切块 → text-embedding-v3 → Chroma。
检索结果强制回带文件名与章节名，让模型的处置建议可追溯到依据。
"""

from __future__ import annotations

from functools import lru_cache

from chromadb.config import Settings as ChromaSettings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from app.config import get_settings


@lru_cache
def get_embeddings() -> OpenAIEmbeddings:
    settings = get_settings()
    return OpenAIEmbeddings(
        model=settings.embed_model,
        api_key=settings.dashscope_api_key,
        base_url=settings.llm_base_url,
        # DashScope 不接受 tiktoken 的 token 计数方式，必须关掉
        check_embedding_ctx_length=False,
        # DashScope 的 embedding 接口单次最多 10 条文本，超过会返回 400：
        # "batch size is invalid, it should not be larger than 10"
        # 这里把批次压到 10，超过的由 langchain 自动拆成多次请求
        chunk_size=10,
    )


@lru_cache
def get_vectorstore() -> Chroma:
    settings = get_settings()
    return Chroma(
        collection_name=settings.collection,
        embedding_function=get_embeddings(),
        persist_directory=settings.chroma_dir,
        # 关掉匿名遥测：一是不往外部发数据，二是 posthog 版本不兼容时会持续打印
        # "Failed to send telemetry event" 噪音，演示和录屏时很难看
        client_settings=ChromaSettings(anonymized_telemetry=False),
    )


_H1_SPLITTER = MarkdownHeaderTextSplitter(
    headers_to_split_on=[("#", "doc"), ("##", "section")],
    strip_headers=False,
)
_CHUNKER = RecursiveCharacterTextSplitter(
    chunk_size=420,
    chunk_overlap=60,
    separators=["\n\n", "\n", "；", "。", " ", ""],
)


def split_markdown(text: str, source: str) -> list[Document]:
    """按 Markdown 标题结构切分，保留可读的溯源信息。"""
    sections = _H1_SPLITTER.split_text(text)
    chunks = _CHUNKER.split_documents(sections)
    for chunk in chunks:
        chunk.metadata["source"] = source
        if chunk.metadata.get("section"):
            chunk.metadata["citation"] = f"{source} · {chunk.metadata['section']}"
        else:
            chunk.metadata["citation"] = source
    return chunks


def search(query: str, k: int | None = None) -> list[Document]:
    settings = get_settings()
    try:
        return get_vectorstore().similarity_search(query, k=k or settings.retrieval_top_k)
    except Exception:  # noqa: BLE001 —— 知识库为空/未初始化时不应让 Agent 崩掉
        return []


def format_hits(docs: list[Document]) -> str:
    """把检索结果整理成模型易读的片段，带来源标注。"""
    if not docs:
        return "未检索到相关的运维 SOP。请基于通用 Linux 知识回答，并明确说明「知识库无对应条目」。"
    blocks = []
    for i, doc in enumerate(docs, start=1):
        citation = doc.metadata.get("citation") or doc.metadata.get("source", "未知来源")
        blocks.append(f"[片段 {i}｜来源：{citation}]\n{doc.page_content.strip()}")
    return "\n\n".join(blocks)
