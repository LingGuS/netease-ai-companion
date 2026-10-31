"""语义检索管道验证：query → 向量化 → Milvus 检索 → 回 PG 补全详情。

本脚本用于验证「向量检索管道」是否打通（第 3 步验收项），
agent 侧的语义搜歌工具在第 5 步接入。

注意：后端运行时会占用 milvus.db，本脚本需在后端停止时运行。

用法:
    cd backend-agent
    python verify_semantic.py "适合雨天的安静情歌"
"""

import logging
import sys

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("verify-semantic")


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "适合雨天的安静情歌"
    from db import get_conn
    from embedder import embed_query
    from milvus_client import SONGS_COLLECTION, ensure_collections, get_milvus_client

    client = get_milvus_client()
    ensure_collections()  # 含 load_collection：进程重开后集合需重新加载
    total = client.get_collection_stats(SONGS_COLLECTION)["row_count"]
    print(f"向量库现有 {total} 首歌曲；检索 query: 「{query}」\n")

    vector = embed_query(query)
    hits = client.search(
        SONGS_COLLECTION,
        data=[vector],
        limit=10,
        output_fields=["name", "artists", "album", "pop"],
    )[0]
    if not hits:
        print("检索无结果（向量库可能为空，请先通过 agent 同步一些歌单/搜索歌曲）")
        return

    # 用 song_id 回 PG 补全详情（Milvus 只存冗余标量，事实源在 PG）
    ids = [h["id"] for h in hits]
    with get_conn() as conn:
        rows = {
            r["song_id"]: r
            for r in conn.execute(
                "SELECT song_id, name, artists, album_name, duration_ms, fee FROM songs WHERE song_id = ANY(%s)",
                (ids,),
            ).fetchall()
        }

    print(f"Top {len(hits)} 检索结果（相似度越高越靠前）：")
    for rank, h in enumerate(hits, 1):
        entity = h.get("entity") or {}
        row = rows.get(h["id"])
        fee_label = {0: "免费", 1: "VIP"}.get(row["fee"] if row else None, "付费")
        print(
            f"{rank:>2}. 《{entity.get('name')}》 - {entity.get('artists')}"
            f" | 专辑《{entity.get('album')}》 | 热度 {entity.get('pop')}"
            f" | {fee_label} | 相似度 {h['distance']:.4f} | song_id={h['id']}"
        )


if __name__ == "__main__":
    main()
