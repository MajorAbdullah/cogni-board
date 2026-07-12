# Cogni Board — Agent Instructions

This is a FastAPI + dc-runtime (React) single-service app for an AI dashboard builder (AMD Developer Hackathon: ACT II).

## Quick Start

```bash
make start          # starts Postgres+Redis (docker compose) + backend on :8000
open http://localhost:8000/Agentic%20Auth.dc.html
```

**Prereqs**: Python 3.12, Docker, `OPENROUTER_API_KEY` in `backend/.env`, Inflectiv account.

## Key Commands

| Command | Purpose |
|---------|---------|
| `make start` | Start dev containers + app (port 8000) |
| `make dev` | Run app only (containers must be up) |
| `make stop` | Stop containers (preserves data) |
| `make install-all` | Full setup: venv, deps, docker images |
| `make env-init` | Scaffold `backend/.env` from `.env.example` |
| `make psql` / `make redis-cli` | Shell into running containers |
| `make db-backup` | Dump Postgres to `backups/` |
| `make clean-volumes` | Destroy containers + volume (destructive) |

Run tests:
```bash
cd backend && python -m pytest -v        # pytest.ini sets pythonpath=., testpaths=tests
bash ../scripts/test_db_smoke.sh         # full E2E against live DB + API (needs running stack)
```

## Architecture (one service, no migrations)

```
frontend/          # dc-runtime SPA (served as static files by FastAPI)
  Agentic Auth.dc.html          # auth + onboarding
  Agentic Dashboard AI.dc.html  # AI dashboard builder
  Agentic App.dc.html           # app shell (profile, datasets, components, insights, team, billing, admin, settings)
  support.js / vendor/          # vendored React runtime (no CDN)
backend/
  main.py           # FastAPI app, routes, startup (init_db), serves frontend/
  routes_app.py     # auth + persistence REST API (/api/auth, /api/me, /api/components, etc.)
  pipeline.py       # agent: plan → retrieve → structure (ChartSpec drafts)
  profiler.py       # dataset profiling → recommended queries + suggested charts
  inflectiv.py      # Inflectiv RAG client (top_k≤20 clamp)
  openrouter.py     # OpenRouter client (fast + strong model split, JSON schema output)
  agentbus.py       # SSE bus + poll fallback for live agent steps
  db.py / db_connector.py  # Postgres layer (auto-create tables, JSONB) + raw PG connector
  cache.py          # Redis cache + in-memory fallback
  auth.py           # JWT tokens, optional_user dependency
  config.py         # all env vars + defaults (INFLECTIV_BASE, OPENROUTER_MODEL_FAST/STRONG, etc.)
```

- **No migrations**: `init_db()` auto-creates tables on boot.
- **Single service**: FastAPI serves both API (`/api/*`) and `frontend/` static files.
- **Infra**: Postgres (port 5433), Redis (port 6380) via `docker-compose.yml`.

## Environment Variables (backend/.env)

| Variable | Required | Default / Notes |
|----------|----------|-----------------|
| `DATABASE_URL` | yes | `postgresql://postgres:ada@localhost:5433/ada` |
| `OPENROUTER_API_KEY` | yes | Required for LLM calls |
| `REDIS_URL` | no | `redis://localhost:6380`; falls back to in-memory |
| `INFLECTIV_BASE` | no | `https://app.inflectiv.ai/api/platform` |
| `OPENROUTER_MODEL_FAST` / `OPENROUTER_MODEL_STRONG` | no | Defaults in `config.py` |
| `DEFAULT_TOP_K` / `DEFAULT_SCORE_THRESHOLD` | no | Retrieval tuning |

Health check: `GET /api/health` → `{ok, openrouter, db, cache}`

## Testing Notes

- **No CI yet** (hackathon gap). All verification is manual / scripted E2E.
- `scripts/test_db_smoke.sh` — full E2E: health → signup (with DB creds) → `/me` → `/my-datasets` → `/session` (DB mode) → `/generate` → `/refine` → workspace roundtrip → `/chat` → guardrails. Run against local stack.
- `scripts/explore_db.sh [table]` — inspect Postgres schema/data.
- Backend tests: `backend/tests/test_guardrails.py` (pytest, run from `backend/`).

## Known Quirks / Gotchas

- **No frontend build step** — UI is plain `.dc.html` + vendored React. Edit files directly.
- **No migrations** — schema changes = edit `db.py` `init_db()` and restart (dev DB is disposable).
- **Ports**: Docker compose uses 5433/6380; README uses 5432/6379. Use compose ports.
- **CORS** is `allow_origins="*"` in dev (see `main.py:40`). Tighten for prod.
- **No email / OAuth / payments** — stubs only (see STATUS.md).
- **Secrets**: `backend/.env` is gitignored. Railway injects `DATABASE_URL`/`REDIS_URL`.
- **Agent bus**: SSE (`/api/agent/events/{job_id}`) with long-poll fallback (`/api/agent/poll`).
- **Provenance**: Every chart component carries `citations[]` + `grounded: true/false` + `confidence`.

## Key Files to Know

| File | Purpose |
|------|---------|
| `backend/main.py` | App entry, routes, health, `/api/generate`, `/api/session` |
| `backend/pipeline.py` | Core agent: plan → retrieve → structure → ChartSpec drafts |
| `backend/profiler.py` | Dataset profiling → suggested queries/charts |
| `backend/agentbus.py` | SSE event bus for live agent steps |
| `backend/routes_app.py` | Auth, user, dashboards, components, insights, workspace, activity, stats |
| `backend/config.py` | All env vars + defaults |
| `docs/ARCHITECTURE.md` | Data flow, API reference |
| `docs/STATUS.md` | Feature status, test status, TODOs |
| `Makefile` | Canonical dev commands |

## Git / Commit Rules

- **Never** mention Claude, Anthropic, or any AI tool in commit messages.
- No `Co-Authored-By: Claude` trailers. Human authorship only (hackathon requirement).