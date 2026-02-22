# Argus

A distributed platform for crawling, storing, and querying academic publication metadata. The system ingests papers from [OpenAlex](https://openalex.org/), constructs a knowledge graph of relationships between papers, authors, institutions, and topics, and exposes the data through a REST API and a Next.js frontend.

---

## Architecture

```
OpenAlex API
     |
     v
Celery Worker (async crawler)
     |
     +---> Postgres (metadata, crawl state)
     +---> Neo4j    (graph: WROTE, CITES, AFFILIATED_WITH, HAS_TOPIC)
                          |
                          v
                    FastAPI server
                          |
                          v
                    Next.js frontend
```

**Components:**

| Service | Role |
|---|---|
| `api` | FastAPI server, exposes REST endpoints |
| `worker` | Celery worker, runs async crawl jobs |
| `postgres` | Relational store for entity metadata and crawl state |
| `neo4j` | Graph store for relationships and traversal queries |
| `redis` | Celery message broker |
| `migrate` | One-shot Alembic migration runner |
| `neo4j-init` | One-shot Neo4j constraint and index setup |

---

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy (async), asyncpg, neo4j driver
- **Crawler:** httpx async client, token-bucket rate limiting, cursor-based pagination
- **Workers:** Celery with Redis broker
- **Databases:** Postgres 16, Neo4j 5 Community
- **Frontend:** Next.js 16, React 19, TypeScript
- **Observability:** Prometheus metrics, structured JSON logging
- **Packaging:** uv / pyproject.toml, Docker Compose

---

## Getting Started

### Prerequisites

- Docker and Docker Compose
- (Frontend only) Bun or Node.js 20+

### Environment variables

Copy the defaults or create a `.env` file at the project root:

```
POSTGRES_PASSWORD=articlegraph
NEO4J_PASSWORD=articlegraph
OPENALEX_EMAIL=your@email.com
```

The `OPENALEX_EMAIL` value is sent as part of the `User-Agent` header to qualify for the OpenAlex polite pool (higher rate limit).

### Start the stack

```bash
docker compose up --build
```

On first boot, `migrate` applies Alembic migrations and `neo4j-init` creates graph constraints and indexes. Both are one-shot services that exit when done.

| Service | URL |
|---|---|
| API | http://localhost:8000 |
| API docs (Swagger) | http://localhost:8000/docs |
| Prometheus metrics | http://localhost:8000/metrics |
| Neo4j browser | http://localhost:7474 |

### Run the frontend

```bash
cd frontend
bun install
bun dev
```

The frontend dev server runs at http://localhost:3000 and proxies API requests to http://localhost:8000.

---

## API Reference

### Papers

```
GET  /papers/search?q=&topic=&year_from=&year_to=&limit=&offset=
GET  /papers/{id}
GET  /papers/{id}/citations?depth=1    # depth: 1–3, default 1
```

### Authors

```
GET  /authors/{id}
GET  /authors/{id}/papers?limit=&offset=
```

### Crawl management

```
POST /crawls                  # start a new crawl job
GET  /crawls/{id}             # get status and progress
POST /crawls/{id}/stop        # stop a running job
POST /crawls/{id}/resume      # resume a stopped or failed job
```

**Start crawl request body:**

```json
{
  "topic_id": "T10014",
  "date_from": "2023-01-01",
  "date_to":   "2024-01-01",
  "institution_id": null,
  "paper_ids": [],
  "incremental": false
}
```

All seed fields are optional and combinable. Set `incremental: true` to only re-ingest records updated since the last crawl run.

### Health

```
GET  /health
```

---

## Project Structure

```
ArticleGraph/
├── backend/
│   ├── api/            FastAPI app, routes, schemas, Prometheus metrics
│   ├── crawler/        OpenAlex HTTP client, extractors, crawl engine
│   ├── db/             SQLAlchemy models, Postgres writer, Neo4j writer and queries
│   ├── migrations/     Alembic migration files
│   ├── shared/         Pydantic settings, structured JSON logger
│   ├── workers/        Celery app and task definitions
│   ├── tests/          Unit and integration tests
│   └── pyproject.toml
├── docker/
│   ├── Dockerfile
│   └── neo4j_constraints.cypher
├── frontend/
│   ├── app/            Next.js App Router pages
│   ├── components/     UI components (search, paper detail, author detail, graph explorer)
│   └── lib/            API client, utilities
└── docker-compose.yml
```

---

## Running Tests

Tests use `pytest` with `pytest-asyncio`. Integration tests spin up real Postgres and Neo4j containers via Testcontainers.

```bash
cd backend
uv run pytest
```

Run a specific test file:

```bash
uv run pytest tests/test_extractors.py
uv run pytest tests/test_writers.py       # requires Docker
uv run pytest tests/test_api.py           # requires Docker
```

---

## Data Model

### Graph (Neo4j)

| Node | Key property |
|---|---|
| `Paper` | `id` (OpenAlex work ID, e.g. `W1234`) |
| `Author` | `id` (OpenAlex author ID, e.g. `A1234`) |
| `Institution` | `id` (OpenAlex institution ID, e.g. `I1234`) |
| `Topic` | `id` (OpenAlex topic ID, e.g. `T1234`) |

| Relationship | Properties |
|---|---|
| `(Author)-[:WROTE]->(Paper)` | — |
| `(Paper)-[:CITES]->(Paper)` | — |
| `(Author)-[:AFFILIATED_WITH]->(Institution)` | `start_year`, `end_year`, `primary` |
| `(Paper)-[:HAS_TOPIC]->(Topic)` | — |

### Relational (Postgres)

| Table | Purpose |
|---|---|
| `papers_metadata` | Paper title, abstract, year, DOI, citation count |
| `authors_metadata` | Author name, ORCID, works count, citation count |
| `institutions_metadata` | Institution name, country, type |
| `crawl_jobs` | Job ID, seed config, status, timestamps |
| `crawl_state` | Cursor, last crawled timestamp, records processed, error log, metrics |

---

## Crawl Mechanics

- Cursor-based pagination — the OpenAlex cursor is persisted in `crawl_state` after each page so crawls are fully resumable across restarts.
- Rate limiting — 10 requests/second per worker using an asyncio semaphore.
- Retry logic — exponential backoff on HTTP 429 and 5xx responses.
- Upsert semantics — all entity writes use `INSERT ... ON CONFLICT DO UPDATE` in Postgres and `MERGE` in Neo4j, making re-ingestion idempotent.
- Incremental mode — filters OpenAlex by `updated_date > last_crawled_at` so only changed records are re-processed.
- Abstract reconstruction — OpenAlex stores abstracts as inverted indexes; the extraction layer reconstructs plain text before storage.
