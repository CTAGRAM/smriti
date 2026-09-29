"""The district side: a plain Qdrant Server collection that devices sync into and
that publishes protocol updates back out to every device."""

import time
import uuid

from qdrant_client import QdrantClient, models

from .device import COLLECTION, NS, now_iso, to_rest
from .embed import DIM, doc_vectors


def client(url: str) -> QdrantClient:
    return QdrantClient(url=url, timeout=10)


def ensure_collection(c: QdrantClient, reset: bool = False):
    if reset and c.collection_exists(COLLECTION):
        c.delete_collection(COLLECTION)
    if c.collection_exists(COLLECTION):
        return
    c.create_collection(
        COLLECTION, shard_number=1,
        vectors_config={"dense": models.VectorParams(size=DIM, distance=models.Distance.COSINE)},
        sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )
    for field in ("kind", "priority", "household", "device", "origin"):
        c.create_payload_index(COLLECTION, field, models.PayloadSchemaType.KEYWORD)
    c.create_payload_index(COLLECTION, "timestamp", models.PayloadSchemaType.FLOAT)
    c.create_payload_index(COLLECTION, "severity", models.PayloadSchemaType.FLOAT)


def publish_protocol(c: QdrantClient, title: str, text: str, tag: str = "protocol") -> str:
    pid = str(uuid.uuid5(NS, f"protocol:{title.lower()}"))
    body = f"{title}: {text}"
    c.upsert(COLLECTION, [models.PointStruct(id=pid, vector=to_rest(doc_vectors(body)), payload={
        "kind": "protocol", "title": title, "text": body, "tag": tag, "origin": "cloud",
        "author": "District health office", "created_at": now_iso(), "timestamp": time.time(), "priority": "normal"})])
    return pid


def overview(c: QdrantClient) -> dict:
    def count(kind=None, **match):
        must = [models.FieldCondition(key=k, match=models.MatchValue(value=v)) for k, v in match.items()]
        if kind:
            must.append(models.FieldCondition(key="kind", match=models.MatchValue(value=kind)))
        return c.count(COLLECTION, count_filter=models.Filter(must=must) if must else None, exact=True).count

    recent, _ = c.scroll(COLLECTION, limit=14, with_payload=True,
                         order_by=models.OrderBy(key="timestamp", direction=models.Direction.DESC))
    urgent, _ = c.scroll(COLLECTION, limit=6, with_payload=True, scroll_filter=models.Filter(must=[
        models.FieldCondition(key="priority", match=models.MatchValue(value="urgent"))]),
        order_by=models.OrderBy(key="timestamp", direction=models.Direction.DESC))
    homes, _ = c.scroll(COLLECTION, limit=50, with_payload=True, scroll_filter=models.Filter(must=[
        models.FieldCondition(key="kind", match=models.MatchValue(value="household"))]))
    return {
        "total": count(), "notes": count("note"), "protocols": count("protocol"), "households": count("household"),
        "urgent": [p.payload for p in urgent],
        "recent": [p.payload for p in recent],
        "conflicts": [{"household": h.payload["household"], **cf} for h in homes for cf in h.payload.get("conflicts", [])],
    }
