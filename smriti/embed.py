"""On-device embeddings. Both run fully offline once the dense model is cached:
a small ONNX sentence model for meaning, and Qdrant Edge's built-in BM25 for keywords."""

from functools import lru_cache

from fastembed import TextEmbedding
from qdrant_edge import Bm25, SparseVector

DENSE_MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384

_bm25 = Bm25()


@lru_cache(maxsize=1)
def _dense() -> TextEmbedding:
    return TextEmbedding(DENSE_MODEL)


def warm() -> None:
    list(_dense().embed(["warm up"]))


def doc_vectors(text: str) -> dict:
    return {"dense": next(iter(_dense().embed([text]))).tolist(), "bm25": _bm25.embed_document(text)}


def query_vectors(text: str) -> tuple[list[float], SparseVector]:
    return next(iter(_dense().query_embed(text))).tolist(), _bm25.embed_query(text)


def dense_many(texts: list[str]) -> list[list[float]]:
    return [v.tolist() for v in _dense().embed(texts)]
