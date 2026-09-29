"""An edge device: two Qdrant Edge shards plus a persistent outbox.

- `local`  (mutable)   — everything written on this device, raw, including names.
- `cloud`  (immutable) — a mirror of the district Qdrant Server, refreshed with
                         partial snapshots. Only redacted data ever reaches it.
Queries run against both and are merged on-device, so search never needs a network.
"""

import json
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import httpx
from qdrant_client import QdrantClient, models
from qdrant_edge import (
    CountRequest, Distance, EdgeConfig, EdgeShard, EdgeSparseVectorParams, EdgeVectorParams,
    FieldCondition, Filter, Fusion, MatchValue, Modifier, PayloadSchemaType, Point, Prefetch, Query,
    QueryRequest, RangeFloat, ScrollRequest, UpdateOperation,
)

from . import jev, policy
from .embed import DIM, doc_vectors, query_vectors

COLLECTION = "smriti"
NS = UUID("6f1c2a3e-9b0d-4c55-8e21-5a7d3b9c0e11")
EDGE_CONFIG = EdgeConfig(
    vectors={"dense": EdgeVectorParams(size=DIM, distance=Distance.Cosine)},
    sparse_vectors={"bm25": EdgeSparseVectorParams(modifier=Modifier.Idf)},
)

now_iso = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731
household_id = lambda hh: str(uuid.uuid5(NS, f"household:{hh}"))  # noqa: E731


def summary(fields: dict, hh: str, with_name: bool) -> str:
    parts = [hh]
    if with_name and fields.get("name"):
        parts.append(fields["name"])
    for key in ("status", "risk", "next_visit", "notes"):
        if fields.get(key):
            parts.append(f"{key.replace('_', ' ')}: {fields[key]}")
    return " · ".join(parts)


INDEXED_FIELDS = {"kind": PayloadSchemaType.Keyword, "priority": PayloadSchemaType.Keyword,
                  "household": PayloadSchemaType.Keyword, "device": PayloadSchemaType.Keyword,
                  "timestamp": PayloadSchemaType.Float, "severity": PayloadSchemaType.Float}


def create_shard(path: str) -> EdgeShard:
    shard = EdgeShard.create(path, EDGE_CONFIG)
    for field, schema in INDEXED_FIELDS.items():
        shard.update(UpdateOperation.create_field_index(field, schema))
    return shard


def build_filter(filters: dict | None) -> Filter | None:
    if not filters:
        return None
    must = [FieldCondition(key=k, match=MatchValue(v)) for k, v in filters.items() if k in ("kind", "priority", "household", "device")]
    if filters.get("since"):
        must.append(FieldCondition(key="timestamp", range=RangeFloat(gte=filters["since"])))
    if filters.get("min_severity") is not None:
        must.append(FieldCondition(key="severity", range=RangeFloat(gte=filters["min_severity"])))
    return Filter(must=must) if must else None


def to_rest(vectors: dict) -> dict:
    sv = vectors["bm25"]
    return {"dense": vectors["dense"], "bm25": models.SparseVector(indices=list(sv.indices), values=list(sv.values))}


class Device:
    def __init__(self, device_id: str, name: str, role: str, root: Path, server_url: str):
        self.id, self.name, self.role = device_id, name, role
        self.dir = root / device_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.server_url = server_url
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.dir / "device.db", check_same_thread=False)
        self.db.executescript("""
            create table if not exists outbox(id text primary key, kind text, priority integer, share text,
              payload text, extra text, reasons text, created_at real, status text, sent_at real);
            create table if not exists events(ts real, kind text, message text);
            create table if not exists register(name text primary key, household text);
            create table if not exists state(key text primary key, value text);
        """)
        local_dir = self.dir / "local"
        local_dir.mkdir(exist_ok=True)
        self.local = EdgeShard.load(str(local_dir)) if any(local_dir.iterdir()) else create_shard(str(local_dir))
        cloud_dir = self.dir / "cloud"
        self.cloud = EdgeShard.load(str(cloud_dir)) if cloud_dir.exists() and any(cloud_dir.iterdir()) else None

    # ── state ───────────────────────────────────────────────────────────────
    def _get(self, key, default=None):
        row = self.db.execute("select value from state where key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set(self, key, value):
        self.db.execute("insert or replace into state values(?,?)", (key, json.dumps(value)))
        self.db.commit()

    @property
    def online(self) -> bool:
        return self._get("online", True)

    def set_online(self, on: bool):
        self._set("online", on)
        self.log("network", "Back online" if on else "Went offline — everything keeps working locally")

    def log(self, kind: str, message: str):
        self.db.execute("insert into events values(?,?,?)", (time.time(), kind, message))
        self.db.commit()

    def events(self, limit=30):
        return [{"ts": ts, "kind": k, "message": m} for ts, k, m in
                self.db.execute("select ts, kind, message from events order by ts desc limit ?", (limit,))]

    @property
    def register(self) -> dict[str, str]:
        return dict(self.db.execute("select name, household from register"))

    def set_register(self, entries: dict[str, str]):
        self.db.executemany("insert or replace into register values(?,?)", entries.items())
        self.db.commit()

    # ── writes ──────────────────────────────────────────────────────────────
    def _enqueue(self, pid, kind, decision_or_none, payload, extra=None, priority="normal", share="redacted", reasons=()):
        self.db.execute("insert or replace into outbox values(?,?,?,?,?,?,?,?,?,?)", (
            pid, kind, 0 if priority == "urgent" else 1, share, json.dumps(payload), json.dumps(extra or {}),
            json.dumps(list(reasons)), time.time(), "local_only" if share == "local_only" else "pending", None))
        self.db.commit()

    def add_note(self, text: str, household: str | None = None, private: bool = False) -> dict:
        with self.lock:
            vectors = doc_vectors(text)
            triage = jev.triage(text)
            d = policy.decide(text, vectors["dense"], self.register, private, triage)
            # Link the note to a household named in it, so household filters find it.
            household = household or next((hh for name, hh in self.register.items()
                                           if re.search(rf"\b{re.escape(name)}\b", text, re.I)), None)
            pid = str(uuid.uuid4())
            base = {"kind": "note", "household": household, "device": self.id, "author": self.name,
                    "created_at": now_iso(), "timestamp": time.time(), "priority": d.priority,
                    "share": d.share, "reasons": d.reasons,
                    **({"severity": triage["severity"], "danger_p": triage["danger"], "follow_up_p": triage["follow_up"]} if triage else {})}
            self.local.update(UpdateOperation.upsert_points([Point(pid, vectors, {**base, "text": text})]))
            self._enqueue(pid, "note", d, {**base, "text": d.redacted_text, "redacted": d.redacted_text != text},
                          priority=d.priority, share=d.share, reasons=d.reasons)
            self.log("memory", f"Saved note · {d.share.replace('_', ' ')}{' · URGENT' if d.priority == 'urgent' else ''}")
            return {"id": pid, "share": d.share, "priority": d.priority, "reasons": d.reasons,
                    "redacted_text": d.redacted_text, "danger_score": round(d.danger_score, 2), "triage": triage}

    def household(self, hh: str) -> dict | None:
        pid = household_id(hh)
        mine = self.local.retrieve([pid], with_payload=True, with_vector=False)
        theirs = self.cloud.retrieve([pid], with_payload=True, with_vector=False) if self.cloud else []
        mine = mine[0].payload if mine else None
        theirs = theirs[0].payload if theirs else None
        if theirs and (not mine or theirs.get("version", 0) >= mine.get("version", 0)):
            name = (mine or {}).get("fields", {}).get("name") or next((n for n, h in self.register.items() if h == hh), None)
            return {**theirs, "fields": {**theirs["fields"], **({"name": name} if name else {})}}
        return mine

    def update_household(self, hh: str, changes: dict, note: str | None = None) -> dict:
        with self.lock:
            current = self.household(hh) or {"fields": {}, "version": 0, "conflicts": []}
            base_version = current.get("version", 0)
            base_fields = {k: current["fields"].get(k) for k in changes}
            fields = {**current["fields"], **changes}
            record = {"kind": "household", "household": hh, "fields": fields, "version": base_version + 1,
                      "updated_by": self.name, "updated_at": now_iso(), "timestamp": time.time(),
                      "conflicts": current.get("conflicts", []), "device": self.id}
            text = summary(fields, hh, with_name=True)
            self.local.update(UpdateOperation.upsert_points([Point(household_id(hh), doc_vectors(text), {**record, "text": text})]))
            self._enqueue(household_id(hh) + f":{time.time()}", "household", None,
                          {"household": hh}, {"changes": changes, "base_fields": base_fields, "base_version": base_version})
            self.log("memory", f"Updated {hh}: {', '.join(f'{k} → {v}' for k, v in changes.items())}")
            return record

    # ── search ──────────────────────────────────────────────────────────────
    def search(self, q: str, kind: str | None = None, limit: int = 8, filters: dict | None = None) -> dict:
        filters = {**(filters or {}), **({"kind": kind} if kind else {})}
        t0 = time.perf_counter()
        dense, sparse = query_vectors(q)
        t1 = time.perf_counter()
        flt = build_filter(filters)
        req = QueryRequest(limit=limit, with_payload=True, query=Fusion.Rrf(k=60), prefetches=[
            Prefetch(limit=limit * 3, query=Query.Nearest(dense, using="dense"), filter=flt),
            Prefetch(limit=limit * 3, query=Query.Nearest(sparse, using="bm25"), filter=flt),
        ])
        hits, seen = [], set()
        shards = [("device", self.local)] + ([("cloud", self.cloud)] if self.cloud else [])
        results = [(src, h) for src, shard in shards for h in shard.query(req)]
        t2 = time.perf_counter()
        for src, h in sorted(results, key=lambda r: (-r[1].score, r[0] != "device")):
            pid = str(h.id)
            if pid in seen:
                continue
            seen.add(pid)
            p = h.payload or {}
            hits.append({"id": pid, "score": round(h.score, 4), "source": src, "kind": p.get("kind"),
                         "text": p.get("text"), "household": p.get("household"), "author": p.get("author") or p.get("updated_by"),
                         "priority": p.get("priority"), "created_at": p.get("created_at") or p.get("updated_at"),
                         "origin": p.get("origin", "device"), "device": p.get("device"), "severity": p.get("severity")})
        return {"hits": hits[:limit], "embed_ms": round((t1 - t0) * 1000, 2), "search_ms": round((t2 - t1) * 1000, 3),
                "online": self.online}

    # ── sync ────────────────────────────────────────────────────────────────
    def _client(self) -> QdrantClient:
        return QdrantClient(url=self.server_url, timeout=10)

    def _upload(self, client: QdrantClient) -> dict:
        rows = self.db.execute("select id, kind, priority, payload, extra from outbox where status='pending' "
                               "order by priority, created_at").fetchall()
        sent, conflicts = [], []
        for oid, kind, priority, payload, extra in rows:
            payload, extra = json.loads(payload), json.loads(extra)
            if kind == "note":
                client.upsert(COLLECTION, [models.PointStruct(id=oid, vector=to_rest(doc_vectors(payload["text"])), payload=payload)])
                sent.append({"kind": "note", "priority": "urgent" if priority == 0 else "normal", "text": payload["text"]})
            else:
                result = self._upload_household(client, payload["household"], extra)
                conflicts += result["conflicts"]
                sent.append({"kind": "household", "priority": "normal", "text": f"{payload['household']} record v{result['version']}"})
            self.db.execute("update outbox set status='sent', sent_at=? where id=?", (time.time(), oid))
            self.db.commit()
        return {"sent": sent, "conflicts": conflicts}

    def _upload_household(self, client: QdrantClient, hh: str, extra: dict) -> dict:
        """Optimistic concurrency with a field-level three-way merge against the server copy."""
        pid = household_id(hh)
        found = client.retrieve(COLLECTION, [pid], with_payload=True)
        server = found[0].payload if found else None
        s_version = server["version"] if server else 0
        s_fields = dict(server["fields"]) if server else {}
        conflicts = [c for c in (server or {}).get("conflicts", []) if c["field"] not in extra["changes"]]
        new_conflicts = []
        for key, mine in extra["changes"].items():
            if key == "name":
                continue  # names never leave the device
            base, theirs = extra["base_fields"].get(key), s_fields.get(key)
            if s_version > extra["base_version"] and theirs != base and theirs != mine:
                c = {"field": key, "mine": mine, "theirs": theirs, "mine_by": self.name,
                     "theirs_by": server.get("updated_by"), "at": now_iso()}
                conflicts.append(c)
                new_conflicts.append({"household": hh, **c})
            else:
                s_fields[key] = mine
        s_fields.pop("name", None)
        version = max(s_version, extra["base_version"]) + 1
        record = {"kind": "household", "household": hh, "fields": s_fields, "version": version,
                  "updated_by": self.name, "updated_at": now_iso(), "timestamp": time.time(),
                  "conflicts": conflicts, "device": self.id, "text": summary(s_fields, hh, with_name=False)}
        client.upsert(COLLECTION, [models.PointStruct(id=pid, vector=to_rest(doc_vectors(record["text"])), payload=record)])
        # Reflect the merged state locally, keeping the name that only this device knows.
        name = next((n for n, h in self.register.items() if h == hh), None)
        local_fields = {**s_fields, **({"name": name} if name else {})}
        text = summary(local_fields, hh, with_name=True)
        self.local.update(UpdateOperation.upsert_points([Point(pid, doc_vectors(text), {**record, "fields": local_fields, "text": text})]))
        for c in new_conflicts:
            self.log("conflict", f"Conflict on {hh}.{c['field']}: you said “{c['mine']}”, {c['theirs_by']} said “{c['theirs']}”")
        return {"version": version, "conflicts": new_conflicts}

    def _pull(self) -> int:
        """Refreshes the cloud mirror: a full shard snapshot the first time, partial snapshots after."""
        base = f"{self.server_url}/collections/{COLLECTION}/shards/0/snapshot"
        cloud_dir = self.dir / "cloud"
        before = self.cloud.info().points_count if self.cloud else 0
        with tempfile.TemporaryDirectory(dir=self.dir) as tmp:
            snap = Path(tmp) / "shard.snapshot"
            with httpx.Client(timeout=60) as http:
                if self.cloud is None:
                    with http.stream("GET", base) as r:
                        r.raise_for_status()
                        with open(snap, "wb") as f:
                            for chunk in r.iter_bytes():
                                f.write(chunk)
                    if cloud_dir.exists():
                        shutil.rmtree(cloud_dir)
                    cloud_dir.mkdir(parents=True)
                    EdgeShard.unpack_snapshot(str(snap), str(cloud_dir))
                    self.cloud = EdgeShard.load(str(cloud_dir))
                    mode = "full snapshot"
                else:
                    with http.stream("POST", f"{base}/partial/create", json=self.cloud.snapshot_manifest()) as r:
                        r.raise_for_status()
                        with open(snap, "wb") as f:
                            for chunk in r.iter_bytes():
                                f.write(chunk)
                    self.cloud.update_from_snapshot(str(snap), tmp_dir=tmp)
                    mode = "partial snapshot"
        after = self.cloud.info().points_count
        self.log("sync", f"Pulled {mode} from district server · cloud memory {before} → {after}")
        return after - before

    def sync(self) -> dict:
        if not self.online:
            raise RuntimeError("Device is offline")
        with self.lock:
            t0 = time.perf_counter()
            client = self._client()
            up = self._upload(client)
            pulled = self._pull()
            ms = round((time.perf_counter() - t0) * 1000)
            urgent = sum(1 for s in up["sent"] if s["priority"] == "urgent")
            self._set("last_sync", now_iso())
            self.log("sync", f"Synced in {ms} ms · sent {len(up['sent'])} ({urgent} urgent first) · {len(up['conflicts'])} conflicts")
            return {**up, "pulled": pulled, "ms": ms}

    # ── inspection ──────────────────────────────────────────────────────────
    def status(self) -> dict:
        count = lambda shard: shard.count(CountRequest(exact=True)) if shard else 0  # noqa: E731
        q = dict(self.db.execute("select status || ':' || priority, count(*) from outbox group by status, priority").fetchall())
        return {"id": self.id, "name": self.name, "role": self.role, "online": self.online,
                "local_points": count(self.local), "cloud_points": count(self.cloud),
                "pending_urgent": q.get("pending:0", 0), "pending_normal": q.get("pending:1", 0),
                "local_only": q.get("local_only:0", 0) + q.get("local_only:1", 0),
                "last_sync": self._get("last_sync")}

    def outbox(self, limit=20):
        rows = self.db.execute("select id, kind, priority, share, payload, reasons, status, created_at from outbox "
                               "order by case status when 'pending' then 0 else 1 end, priority, created_at desc limit ?", (limit,))
        return [{"id": i, "kind": k, "priority": "urgent" if p == 0 else "normal", "share": s,
                 "text": json.loads(pl).get("text") or json.loads(pl).get("household"), "reasons": json.loads(r),
                 "status": st, "created_at": c} for i, k, p, s, pl, r, st, c in rows]

    def households(self) -> list[dict]:
        ids = sorted(set(self.register.values()))
        homes = [h for h in (self.household(hh) for hh in ids) if h]
        risky = lambda h: "high" in str(h["fields"].get("risk", "")).lower()  # noqa: E731
        return sorted(homes, key=lambda h: (not h.get("conflicts"), not risky(h), h["household"]))

    def local_memories(self, limit=40):
        recs, _ = self.local.scroll(ScrollRequest(limit=limit, with_payload=True))
        return [{"id": str(r.id), **(r.payload or {})} for r in recs]
