"""On-device scale test: fills a fresh Edge shard with realistic visit notes, builds
the HNSW index in-process, and times hybrid search with and without payload filters."""

import json
import random
import shutil
import statistics
import tempfile
import time
from pathlib import Path

from qdrant_edge import Bm25, Fusion, Point, Prefetch, Query, QueryRequest, UpdateOperation

from .device import build_filter, create_shard
from .embed import dense_many, query_vectors

TEMPLATES = [
    "{hh}: pregnant, {m} months, came for check-up; blood pressure normal, iron tablets given",
    "{hh}: complains of severe headache and blurred vision since morning, feet swollen",
    "{hh}: newborn day {d}, weighed {w} kg, breastfeeding well, cord clean",
    "{hh}: baby not feeding since last night, very sleepy, mother worried",
    "{hh}: child with loose stools {d} times today, ORS and zinc started",
    "{hh}: fever for {d} days near the pond, advised malaria rapid test",
    "{hh}: measles-rubella vaccine given, next dose card updated",
    "{hh}: missed third antenatal check-up, family asked to visit PHC on Monday",
    "{hh}: bleeding reported in seventh month, arranged vehicle to PHC",
    "{hh}: elderly member with cough for three weeks, referred for TB sputum test",
    "{hh}: kangaroo care demonstrated for low birth weight baby, {w} kg",
    "{hh}: family says drinking water looks muddy after rain, advised boiling",
    "{hh}: counselling on family planning, wants follow-up next month",
    "{hh}: child 2 years, weight below curve, referred to anganwadi nutrition programme",
    "{hh}: heat exhaustion in farm worker, given ORS and told to rest in shade",
]
QUERIES = [
    "pregnant woman with danger signs", "baby not feeding well", "fever cases near water",
    "who missed antenatal visits", "diarrhoea in small children", "low birth weight babies",
    "vaccination done", "referral to PHC needed", "cough for many weeks", "dirty drinking water",
]


def _pct(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * p))], 3)


def cached(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def run(path: Path, n: int = 10000) -> dict:
    rnd = random.Random(7)
    households = [f"HH-{i:03d}" for i in range(1, 401)]
    now = time.time()
    docs = []
    for i in range(n):
        hh = rnd.choice(households)
        text = rnd.choice(TEMPLATES).format(hh=hh, m=rnd.randint(3, 9), d=rnd.randint(1, 28), w=round(rnd.uniform(1.6, 3.4), 1))
        docs.append((text, {"kind": "note", "household": hh, "priority": "urgent" if rnd.random() < 0.08 else "normal",
                            "device": "bench", "timestamp": now - rnd.uniform(0, 180) * 86400, "text": text}))
    tmp = tempfile.mkdtemp(prefix="smriti-bench-")
    try:
        shard = create_shard(tmp)
        t0 = time.perf_counter()
        dense = dense_many([d[0] for d in docs])
        bm25 = Bm25()
        embed_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        for start in range(0, n, 1000):
            batch = docs[start:start + 1000]
            shard.update(UpdateOperation.upsert_points([
                Point(start + i, {"dense": dense[start + i], "bm25": bm25.embed_document(text)}, payload)
                for i, (text, payload) in enumerate(batch)]))
        insert_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        shard.optimize()
        index_s = time.perf_counter() - t0

        def timed(q, filters):
            dv, sv = query_vectors(q)
            flt = build_filter(filters)
            req = QueryRequest(limit=8, with_payload=False, query=Fusion.Rrf(k=60), prefetches=[
                Prefetch(limit=24, query=Query.Nearest(dv, using="dense"), filter=flt),
                Prefetch(limit=24, query=Query.Nearest(sv, using="bm25"), filter=flt)])
            t = time.perf_counter()
            shard.query(req)
            return (time.perf_counter() - t) * 1000

        for q in QUERIES[:3]:  # warm caches
            timed(q, None)
        plain, filtered = [], []
        for _ in range(5):
            for q in QUERIES:
                plain.append(timed(q, None))
                filtered.append(timed(q, {"household": rnd.choice(households), "kind": "note", "since": now - 30 * 86400}))
        size_mb = sum(f.stat().st_size for f in Path(tmp).rglob("*") if f.is_file()) / 1e6
        shard.close()
        result = {
            "points": n, "embed_per_s": round(n / embed_s), "insert_s": round(insert_s, 2), "index_s": round(index_s, 2),
            "hybrid_p50_ms": _pct(plain, 0.5), "hybrid_p95_ms": _pct(plain, 0.95),
            "filtered_p50_ms": _pct(filtered, 0.5), "filtered_p95_ms": _pct(filtered, 0.95),
            "filter": "household + kind + last 30 days", "disk_mb": round(size_mb, 1), "ran_at": time.strftime("%Y-%m-%d %H:%M"),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result))
    return result
