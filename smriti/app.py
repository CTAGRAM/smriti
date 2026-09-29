import os
import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import bench, cloud_ai, query, seed, server
from .device import Device
from .embed import warm

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "devices"
SERVER_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")

app = FastAPI(title="Smriti")
devices: dict[str, Device] = {}


def load_devices():
    for did, name, role in (seed.ASHA, seed.ANM):
        devices[did] = Device(did, name, role, DATA, SERVER_URL)


def reset_demo():
    for d in devices.values():
        d.local.close()
        if d.cloud:
            d.cloud.close()
    devices.clear()
    shutil.rmtree(DATA, ignore_errors=True)
    c = server.client(SERVER_URL)
    server.ensure_collection(c, reset=True)
    for title, text in seed.PROTOCOLS:
        server.publish_protocol(c, title, text)
    load_devices()
    asha, anm = devices[seed.ASHA[0]], devices[seed.ANM[0]]
    asha.set_register(seed.REGISTERS[asha.id])
    anm.set_register(seed.REGISTERS[anm.id])
    for hh, fields in seed.HOUSEHOLDS.items():
        asha.update_household(hh, fields)
    for text, hh, private in seed.ASHA_NOTES:
        asha.add_note(text, hh, private)
    for text, hh, private in seed.ANM_NOTES:
        anm.add_note(text, hh, private)
    asha.sync()
    anm.sync()
    asha.sync()
    for d in (asha, anm):
        d.db.execute("delete from events")
        d.db.commit()
        d.log("network", "Demo ready · synced with the district server")


@app.on_event("startup")
def startup():
    warm()
    c = server.client(SERVER_URL)
    if not c.collection_exists("smriti") or not DATA.exists():
        reset_demo()
    else:
        load_devices()


def dev(did: str) -> Device:
    if did not in devices:
        raise HTTPException(404, "Unknown device")
    return devices[did]


class Online(BaseModel):
    online: bool


class Note(BaseModel):
    text: str
    household: str | None = None
    private: bool = False


class Changes(BaseModel):
    changes: dict


class Ask(BaseModel):
    question: str


class Protocol(BaseModel):
    title: str
    text: str


@app.get("/api/state")
def state():
    return {"devices": [d.status() for d in devices.values()], "cloud": server.overview(server.client(SERVER_URL))}


@app.get("/api/devices/{did}")
def device_detail(did: str):
    d = dev(did)
    return {"status": d.status(), "outbox": d.outbox(), "events": d.events(), "households": d.households()}


@app.post("/api/devices/{did}/online")
def set_online(did: str, body: Online):
    dev(did).set_online(body.online)
    return dev(did).status()


@app.post("/api/devices/{did}/notes")
def add_note(did: str, body: Note):
    return dev(did).add_note(body.text.strip(), body.household, body.private)


@app.post("/api/devices/{did}/households/{hh}")
def update_household(did: str, hh: str, body: Changes):
    return dev(did).update_household(hh, body.changes)


@app.get("/api/devices/{did}/search")
def search(did: str, q: str, kind: str | None = None):
    d = dev(did)
    text, filters, chips = query.parse(q, d.register)
    if kind:
        filters["kind"] = kind
        chips = [c for c in chips if not c.startswith("kind")] + [f"kind = {kind}"]
    return {**d.search(text, limit=8, filters=filters), "filters": chips, "query_text": text}


@app.get("/api/benchmark")
def get_benchmark():
    return bench.cached(ROOT / "data" / "bench.json")


@app.post("/api/benchmark")
def run_benchmark(n: int = 10000):
    return bench.run(ROOT / "data" / "bench.json", n)


@app.post("/api/devices/{did}/sync")
def sync(did: str):
    try:
        return dev(did).sync()
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.post("/api/devices/{did}/ask")
def ask(did: str, body: Ask):
    d = dev(did)
    text, filters, _ = query.parse(body.question, d.register)
    found = d.search(text, limit=6, filters={k: v for k, v in filters.items() if k == "household"})
    if not d.online:
        return {"offline": True, "hits": found["hits"], "search_ms": found["search_ms"]}
    return {"offline": False, "hits": found["hits"], "search_ms": found["search_ms"],
            **cloud_ai.answer(body.question, found["hits"], d.register)}


@app.post("/api/cloud/protocols")
def publish(body: Protocol):
    return {"id": server.publish_protocol(server.client(SERVER_URL), body.title.strip(), body.text.strip())}


@app.post("/api/demo/reset")
def demo_reset():
    reset_demo()
    return state()


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")
