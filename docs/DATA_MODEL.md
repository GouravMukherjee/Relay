# Relay — Data Model

**Status:** Draft for build · **Owner:** Systems/Backend · **Last updated:** June 2026

PostgreSQL 15 + pgvector. Moss holds the production retrieval index; Postgres is the system of record (and the pgvector fallback). Single-org assumption for the hackathon — `organization_id` is included for forward-compatibility but defaulted.

---

## Entity overview

```
organizations 1───* documents 1───* chunks
organizations 1───* users
sessions 1───* utterances
sessions 1───* cards
cards *───* chunks        (via card_sources)
customers 1───* memories
customers 1───* sessions  (optional, Desk)
sessions 1───1 leads      (Intake)
```

---

## Tables

### organizations
| column | type | notes |
|--------|------|-------|
| id | uuid PK | `org_…` |
| name | text | |
| created_at | timestamptz | default now() |

### users
| column | type | notes |
|--------|------|-------|
| id | uuid PK | `usr_…` |
| organization_id | uuid FK | |
| name | text | |
| role | text | |

### documents
| column | type | notes |
|--------|------|-------|
| id | uuid PK | `doc_…` |
| organization_id | uuid FK | |
| title | text | |
| source_type | text | pdf \| docx \| txt |
| status | text | processing \| ready \| failed |
| tags | text[] | |
| chunk_count | int | |
| created_at | timestamptz | |

### chunks
| column | type | notes |
|--------|------|-------|
| id | uuid PK | `chk_…` |
| document_id | uuid FK | |
| ordinal | int | position in doc |
| text | text | chunk content |
| embedding | vector(1024) | pgvector fallback index |
| moss_ref | text | id/handle in the Moss index |
| metadata | jsonb | page, section, table flags |

> Index: `ivfflat (embedding vector_cosine_ops)` for fallback. Primary path queries Moss by `moss_ref`.

### sessions
| column | type | notes |
|--------|------|-------|
| id | uuid PK | `ses_…` |
| organization_id | uuid FK | |
| mode | text | live \| desk \| intake |
| customer_id | uuid FK null | Desk/Intake |
| livekit_room | text null | |
| status | text | active \| ended |
| started_at / ended_at | timestamptz | |

### utterances
| column | type | notes |
|--------|------|-------|
| id | uuid PK | `utt_…` |
| session_id | uuid FK | |
| speaker | text | e.g. rep \| prospect \| customer |
| text | text | final transcript |
| ts | timestamptz | |

### cards
| column | type | notes |
|--------|------|-------|
| id | uuid PK | `card_…` |
| session_id | uuid FK | |
| mode | text | |
| answer | text | grounded, synthesized |
| trigger_text | text | what prompted it |
| latency_ms | int | trigger → render |
| created_at | timestamptz | |

### card_sources (join: card ↔ chunk)
| column | type | notes |
|--------|------|-------|
| card_id | uuid FK | |
| chunk_id | uuid FK | |
| score | float | retrieval score |
| PK | (card_id, chunk_id) | |

### customers
| column | type | notes |
|--------|------|-------|
| id | uuid PK | `cus_…` |
| organization_id | uuid FK | |
| name / company / email | text | |

### memories (cross-session, Desk/Intake)
| column | type | notes |
|--------|------|-------|
| id | uuid PK | `mem_…` |
| customer_id | uuid FK | |
| kind | text | fact \| summary \| preference |
| text | text | |
| embedding | vector(1024) | semantic recall |
| source_session_id | uuid FK null | |
| created_at | timestamptz | |

### leads (Intake)
| column | type | notes |
|--------|------|-------|
| id | uuid PK | `lead_…` |
| session_id | uuid FK | |
| name / company / email | text | |
| qualifiers | jsonb | budget, timeline, need |
| score | int | 0–100 (ICP fit) |
| status | text | hot \| warm \| cold |
| routed_to | text null | channel/owner |
| created_at | timestamptz | |

---

## Retrieval data flow

1. **Ingest:** `documents` row (status=processing) → Unsiloed parse → `chunks` rows → embed → write to Moss (`moss_ref`) + pgvector (`embedding`) → status=ready.
2. **Query:** text → Moss top-k → resolve `moss_ref` → `chunks` → orchestrator → `cards` + `card_sources`.
3. **Fallback:** Moss unavailable → pgvector cosine search over `chunks.embedding`.

## Related docs

`API_SPEC.md` · `TECHNICAL_DESIGN.md`
