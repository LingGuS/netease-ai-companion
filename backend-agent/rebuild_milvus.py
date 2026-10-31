"""全量重建 Milvus songs 集合（派生索引，可随时从 PG 重建）。

体现「PG 是事实源，Milvus 是派生索引」的设计：删掉向量库也不丢数据，
重跑本脚本即可从 PG 完整恢复。

注意：Milvus Lite 的 .db 文件同一时刻只能被一个进程打开，
运行前请先停止 FastAPI 后端。

用法:
    cd backend-agent
    python rebuild_milvus.py
"""

import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("rebuild-milvus")

BATCH_SIZE = 500


def main():
    from db import get_conn
    from embedder import build_song_embed_text, embed_texts
    from milvus_client import SONGS_COLLECTION, ensure_collections, get_milvus_client, upsert_song_vectors

    client = get_milvus_client()
    if client.has_collection(SONGS_COLLECTION):
        client.drop_collection(SONGS_COLLECTION)
        logger.info("已删除旧集合 %s", SONGS_COLLECTION)
    ensure_collections()

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT song_id, name, artists, album_name, pop, fee
            FROM songs ORDER BY song_id
            """
        ).fetchall()
        conn.execute("UPDATE songs SET embedded = false")
    total = len(rows)
    logger.info("从 PG 读取 %d 首歌曲，开始重建向量索引...", total)

    start = time.perf_counter()
    done = 0
    for i in range(0, total, BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        texts = [build_song_embed_text(r["name"], r["artists"], r["album_name"]) for r in batch]
        vectors = embed_texts(texts)
        payload = [
            {
                "id": r["song_id"],
                "vector": vec,
                "name": r["name"],
                "artists": "/".join(r["artists"]),
                "album": r["album_name"] or "",
                "fee": r["fee"] or 0,
                "pop": r["pop"] or 0,
            }
            for r, vec in zip(batch, vectors)
        ]
        upsert_song_vectors(payload)
        with get_conn() as conn:
            conn.execute(
                "UPDATE songs SET embedded = true WHERE song_id = ANY(%s)",
                ([r["song_id"] for r in batch],),
            )
        done += len(batch)
        logger.info("进度: %d/%d", done, total)

    logger.info("重建完成：%d 首，耗时 %.1fs", done, time.perf_counter() - start)


if __name__ == "__main__":
    main()
