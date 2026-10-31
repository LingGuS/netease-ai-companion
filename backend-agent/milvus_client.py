"""Milvus 本地客户端：进程内单例 + 集合初始化。

Milvus Lite 是嵌入式库（本地文件存储），同一文件同一时刻只能被一个进程
打开——后端运行期间 rebuild/verify 等脚本不要并行操作同一个 .db 文件。
"""

import logging
import time
from pathlib import Path

from pymilvus import DataType, MilvusClient

from config import get_settings

logger = logging.getLogger(__name__)

SONGS_COLLECTION = "songs"
TASTE_COLLECTION = "user_taste"

_client: MilvusClient | None = None


def get_milvus_client() -> MilvusClient:
    """进程级单例：Windows 下文件句柄释放有延迟，不做频繁开关。"""
    global _client
    if _client is None:
        settings = get_settings()
        path = Path(settings.milvus_db_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / path
        path.parent.mkdir(parents=True, exist_ok=True)
        _client = MilvusClient(str(path))
        logger.info("Milvus Lite 已连接: %s", path)
    return _client


def ensure_collections() -> None:
    """幂等创建 songs / user_taste 两个集合（已存在则跳过）。"""
    settings = get_settings()
    client = get_milvus_client()
    dim = settings.embedding_dim

    if not client.has_collection(SONGS_COLLECTION):
        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.INT64, is_primary=True)  # = 网易云 song_id
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)
        # 冗余标量：检索结果直接展示/过滤，免回查 PG
        schema.add_field("name", DataType.VARCHAR, max_length=256)
        schema.add_field("artists", DataType.VARCHAR, max_length=512)
        schema.add_field("album", DataType.VARCHAR, max_length=256)
        schema.add_field("fee", DataType.INT16)
        schema.add_field("pop", DataType.INT16)
        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="vector", index_type="HNSW", metric_type="COSINE",
            params={"M": 16, "efConstruction": 200},
        )
        client.create_collection(SONGS_COLLECTION, schema=schema, index_params=index_params)
        logger.info("已创建 Milvus 集合 %s (dim=%d)", SONGS_COLLECTION, dim)

    if not client.has_collection(TASTE_COLLECTION):
        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.INT64, is_primary=True)  # = 网易云 netease_uid
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)
        schema.add_field("updated_at", DataType.INT64)  # epoch 秒
        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="vector", index_type="HNSW", metric_type="COSINE",
            params={"M": 16, "efConstruction": 200},
        )
        client.create_collection(TASTE_COLLECTION, schema=schema, index_params=index_params)
        logger.info("已创建 Milvus 集合 %s (dim=%d)", TASTE_COLLECTION, dim)

    # 检索前必须把集合加载进内存（新建/重开进程后集合可能处于 released 状态）
    for name in (SONGS_COLLECTION, TASTE_COLLECTION):
        client.load_collection(name)


def upsert_song_vectors(rows: list[dict]) -> int:
    """写入/更新歌曲向量。rows: [{id, vector, name, artists, album, fee, pop}]"""
    if not rows:
        return 0
    client = get_milvus_client()
    client.upsert(SONGS_COLLECTION, rows)
    return len(rows)


def upsert_taste_vector(uid: int, vector: list[float]) -> None:
    """写入/更新用户画像向量（第 4 步填充）。"""
    get_milvus_client().upsert(
        TASTE_COLLECTION, [{"id": uid, "vector": vector, "updated_at": int(time.time())}]
    )


def search_songs(vector: list[float], limit: int = 10) -> list[dict]:
    """歌曲集合相似检索：返回 [{id, distance, entity:{name,artists,album,fee,pop}}]。"""
    return get_milvus_client().search(
        SONGS_COLLECTION,
        data=[vector],
        limit=limit,
        output_fields=["name", "artists", "album", "fee", "pop"],
    )[0]


def get_taste_vector(uid: int) -> list[float] | None:
    """取用户画像向量；不存在返回 None。"""
    rows = get_milvus_client().get(TASTE_COLLECTION, ids=[uid], output_fields=["vector"])
    if not rows:
        return None
    return rows[0].get("vector")
