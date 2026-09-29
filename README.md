# Smriti — memory that works without a signal

**स्मृति (smriti) means “memory”.** India's ~10 lakh ASHA workers keep track of every pregnancy, newborn and fever in their village — often where there is no mobile signal, and always with data that should not leave the phone.

Smriti is an offline-first AI memory for frontline health workers. Every device carries its own **Qdrant Edge** shard: visit notes, household records and health protocols are searchable in about a millisecond with no network. When a connection appears, Smriti decides on the device what may leave it, sends danger signs first, merges conflicting edits from other workers, and pulls down district updates.

Built for the Qdrant Edge problem statement at **Code Cubicle 6.0**.

## The edge-to-cloud workflow

```mermaid
flowchart LR
  subgraph Phone["ASHA phone — Qdrant Edge"]
    W[Visit note / record edit] --> P{On-device sync policy}
    P -->|names → household IDs, numbers masked| O[(Persistent outbox<br/>urgent first)]
    P -->|private| L[(local only)]
    W --> M[(Mutable shard<br/>raw, with names)]
    I[(Immutable shard<br/>district mirror)]
    Q[Hybrid search<br/>dense + BM25 + filters] --> M & I
  end
  O -- "upsert (redacted) + 3-way merge" --> S[(Qdrant Server<br/>district)]
  S -- "partial snapshot" --> I
  D[District office] -- "publish protocol" --> S
  Q -. "online only, redacted context" .-> G[Cloud LLM]
```

- **Two shards per device**, following Qdrant's edge synchronisation pattern: a *mutable* shard for everything written on the device, and an *immutable* shard that mirrors the district server through **partial snapshots** (`snapshot_manifest()` → `POST …/snapshot/partial/create` → `update_from_snapshot()`). Queries run on both and merge on-device.
- **Hybrid search offline**: a small ONNX sentence model (`bge-small-en-v1.5`, via FastEmbed) for meaning plus Qdrant Edge's **built-in BM25** for exact terms, fused with RRF.
- **Payload filters, parsed on-device**: “urgent notes about Meena this week” becomes `household = HH-014 · priority = urgent · kind = note · timestamp ≥ 7 days`. The name resolves through the phone's own register, so this works offline. Filtered fields are payload-indexed on the edge shard.
- **Deciding what stays local** — on the device, before anything is queued:
  - registered names become household IDs and phone/Aadhaar numbers are masked; the raw note never leaves the phone;
  - notes semantically close to a danger sign (e.g. *severe headache with blurred vision in pregnancy*) are marked **urgent and sync first**;
  - notes marked private never sync.
- **Intermittent connectivity**: the outbox is SQLite, so nothing is lost if the app closes offline.
- **Conflicting information**: household records use optimistic concurrency with a **field-level three-way merge**. Changes to different fields merge automatically; the same field changed differently on two devices is kept as a visible conflict with both values and authors until someone resolves it — and the resolution syncs like any other edit.
- **Cloud → edge**: the district publishes a protocol (e.g. a heatwave advisory) to the server; every device receives it on its next partial snapshot and can search it offline.
- **Cloud AI, privacy-preserving**: retrieval always happens on the device. When online, a cloud model can write an answer from the top results — but it only ever receives redacted text, and the UI shows exactly what was sent. Offline, Smriti answers from memory alone.

## System One triage with OpenJev (on-device, optional)

Every note also gets a **typed triage** from [OpenJev](https://github.com/razorback16/openjev) — an open, Jev-compatible *System One* server. A small encoder model answers typed questions with probabilities instead of generating text, so an answer cannot go off-schema:

| Question | Type | Used for |
|---|---|---|
| Does the note describe a danger sign that needs referral today? | yes/no → P | urgency (together with semantic similarity to danger signs) |
| How serious is it? `routine · watch · refer soon · emergency` | score → 0–3 | stored as an indexed `severity` payload — “serious cases this week” filters `severity ≥ 2` offline |
| Does the household need another visit within two days? | yes/no → P | shown to the worker |

Smriti runs the **Laya** model (421M, ModernBERT-large, by Nandakishor M / Convai Innovations) on the **CPU**, next to the app — no network. If no OpenJev server is running, Smriti works exactly the same without the triage.

**Why privacy is not left to the model.** We evaluated both of OpenJev's CPU-sized models on real Smriti notes before wiring them in:

| On-device, CPU (M4) | Latency / note | Danger sign | Personal data present | Share decision |
|---|---|---|---|---|
| Verdict 1.4 (151M) | 130–265 ms | no separation (0.62–0.66 on every note) | no separation | 5/5 top choice, very low confidence |
| Laya 1.0 (421M) | 360–570 ms | ranks correctly (0.54 urgent vs 0.15 routine), weak margin | missed names (0.01–0.22) | 4/5 |

So Smriti uses System One where it helps — graded triage that becomes filterable memory — and keeps redaction **rule-based and auditable** (the household register plus number masking). A borderline semantic match (≥ 0.60) becomes urgent only when System One agrees (P ≥ 0.5).

```bash
git clone https://github.com/razorback16/openjev && cd openjev
pip install -e '.[laya]'
OPENJEV_BACKEND=laya OPENJEV_PORT=8093 OPENJEV_DEVICE=cpu python -m openjev
# Smriti picks it up at http://127.0.0.1:8093 (override with OPENJEV_URL)
```

## Measured on-device

A fresh Edge shard filled with realistic visit notes (`POST /api/benchmark`, MacBook, CPU only):

| | |
|---|---|
| Memories | 10,000 |
| HNSW index build (`optimize()`, in-process) | 1.11 s |
| Hybrid search p50 / p95 | 0.343 / 0.578 ms |
| Filtered hybrid search (household + kind + last 30 days) p50 / p95 | 0.165 / 1.384 ms |
| Embedding throughput (CPU) | 183 notes/s |

## Run it

Requires [uv](https://docs.astral.sh/uv/). The Qdrant Server binary is downloaded on first run.

```bash
cp .env.example .env      # GEMINI_API_KEY is optional — only the online “ask” uses it
./run.sh                  # starts Qdrant Server 1.19 + the app on http://localhost:8000
```

The demo loads a village with two workers — Sunita (ASHA, phone) and Rekha (ANM, tablet) — and a district server.

**Try this:** switch Sunita **offline** → write *“Meena has had a severe headache and blurred vision since morning”* → see the policy decision → search *“urgent notes about Meena this week”* (sub-millisecond, no network) → on Rekha's tablet change Meena's next visit and sync → on Sunita's phone change it too → bring Sunita **online** and sync: the urgent note goes first, the conflict appears on both sides → resolve it → publish a heatwave advisory from the district and sync Rekha's tablet.

## Project layout

```
smriti/device.py    edge device: two shards, outbox, hybrid search, sync, three-way merge
smriti/policy.py    on-device sync policy (redaction, danger-sign urgency, private notes)
smriti/jev.py       System One triage through a local OpenJev server
smriti/query.py     on-device query → Qdrant payload filters
smriti/server.py    district collection, protocol publishing, overview
smriti/cloud_ai.py  optional cloud answer over redacted context
smriti/bench.py     on-device scale test
web/index.html      the interface (no external requests — it works offline too)
```

## Limits

- Both devices run in one process to make the demo visible side by side; each has its own shards, outbox and state on disk.
- The merge runs on the device against the server copy, without server-side transactions; two devices syncing the same record in the same instant could race.
- Name redaction uses the device's household register; names not in the register are not detected.
- Protocol texts are short sample summaries for the demo, not medical guidance.

Built with Qdrant Edge, Qdrant Server, FastEmbed, OpenJev (Laya), FastAPI and Gemini (optional). Coded with Claude Code.
