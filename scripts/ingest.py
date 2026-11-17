"""知识库入库脚本。

用法：
    python -m scripts.ingest              # 全量重建
    python -m scripts.ingest --dry-run    # 只看切成多少块，不写向量库

切分策略：先按 Markdown 标题切（保证一个块不跨故障条目），再按长度二次切，
这样每个 chunk 的语义边界是干净的，检索命中率明显高于无脑按字数切。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.rag.retriever import get_vectorstore, split_markdown  # noqa: E402

KNOWLEDGE_DIR = ROOT / "knowledge"


def main() -> int:
    parser = argparse.ArgumentParser(description="构建运维知识库向量索引")
    parser.add_argument("--dry-run", action="store_true", help="只切分不入库")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.dashscope_api_key:
        print("[错误] 未配置 DASHSCOPE_API_KEY，请在 .env 中填写")
        return 1

    files = sorted(KNOWLEDGE_DIR.glob("*.md"))
    if not files:
        print(f"[错误] {KNOWLEDGE_DIR} 下没有 .md 文件")
        return 1

    all_chunks = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        chunks = split_markdown(text, source=path.name)
        all_chunks.extend(chunks)
        print(f"  {path.name:<32} {len(chunks):>3} 块")

    print(f"\n共 {len(files)} 个文件，{len(all_chunks)} 个文本块")

    if args.dry_run:
        print("\n[dry-run] 样例块：")
        print(all_chunks[0].page_content[:300])
        return 0

    store = get_vectorstore()
    store.reset_collection()          # 全量重建，避免旧数据残留导致检索结果漂移
    batches = (len(all_chunks) + 9) // 10
    print(f"\n正在生成向量（接口限制每次 10 条，共 {batches} 次请求）...")
    store.add_documents(all_chunks)
    print(f"\n[完成] 已写入集合 `{settings.collection}`，目录：{settings.chroma_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
