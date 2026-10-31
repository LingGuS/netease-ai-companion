"""向量化模块：bge-small-zh-v1.5 本地编码（CPU）。

模型懒加载：首次调用才初始化并加载权重，避免拖慢服务启动。
首次运行会从 HuggingFace 下载约 100MB；国内网络可设置环境变量
HF_ENDPOINT=https://hf-mirror.com 走镜像。
"""

import logging
import os

from config import get_settings

logger = logging.getLogger(__name__)

_model = None  # 进程级缓存（延迟初始化）


def _get_model():
    global _model
    if _model is None:
        # 国内网络直连 HuggingFace 常超时，未显式配置 HF_ENDPOINT 时默认走镜像。
        # 必须在 import sentence_transformers 之前设置（huggingface_hub 在导入时读取）。
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        from sentence_transformers import SentenceTransformer

        settings = get_settings()
        _model = SentenceTransformer(settings.embedding_model, device="cpu")
        logger.info(
            "Embedding 模型加载完成: %s (dim=%d)",
            settings.embedding_model,
            _model.get_sentence_embedding_dimension(),
        )
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量编码文本，返回归一化后的向量列表。"""
    if not texts:
        return []
    vectors = _get_model().encode(
        texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False
    )
    return [v.tolist() for v in vectors]


def embed_query(text: str) -> list[float]:
    """编码检索 query。

    bge-zh-v1.5 官方建议短查询加检索指令前缀，可提升召回质量。
    """
    return embed_texts([f"为这个句子生成表示以用于检索相关文章：{text}"])[0]


def build_song_embed_text(name: str, artists: list[str], album: str | None) -> str:
    """歌曲的向量化输入文本（极简版：歌名 + 歌手 + 专辑）。"""
    artist_str = "/".join(artists) if artists else "未知歌手"
    album_str = f" 专辑《{album}》" if album else ""
    return f"歌曲《{name}》{artist_str}{album_str}"
