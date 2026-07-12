# Project Status — Cogni Board

_Last updated: 2026-07-12_

A living reference of what's built, what's partial, what's left, and how each area was
tested. Legend: ✅ done & verified · 🟡 partial / by-design · ⬜ not started.

---

## 1. Feature status

### Authentication & onboarding
| Feature | State | Notes |
|---|---|---|
| Sign up (real account in Postgres) | ✅ | bcrypt password, bearer token, supports both Inflectiv + direct DB mode |
| Sign in / sign out | ✅ | token in `localStorage`, `current_user` dependency |
| Auth-gate on the app shell | ✅ | no token → redirect to sign-in |
| 5-step onboarding wizard | ✅ | account → company → connect data → goals → AI setup |
| Onboarding captures Inflectiv key + dataset | ✅ | step 3: paste key → live dataset dropdown |
| Onboarding captures direct DB connection | ✅ | step 3: paste connection string → live table picker |
| Forgot / reset password | 🟡 | backend stubs; reset sets a new password, **no real email, no token check** |
| SSO (Google / Microsoft / Slack) | 🟡 | buttons navigate into the app; **no real OAuth** |

### Dashboard / AI agent (core)
| Feature | State | Notes |
|---|---|---|
| Auto-connect on login | ✅ | uses the user's stored key via `/api/me` → `/api/session` |
| Per-dataset recommended queries | ✅ | from the connect-time dataset profile |
| One-prompt generation (plan→retrieve→structure) | ✅ | real ChartSpec drafts |
| Live agent reasoning (SSE) | ✅ | real steps + retrieval relevance streamed |
| Provenance per component | ✅ | source chunks + grounded/estimated badge |
| Confidence from real retrieval scores | ✅ | rings reflect avg relevance |
| Conversational drill-down (`/refine`) | ✅ | follow-up adds a cited component |
| Direct chat Q&A (`/api/chat`) | ✅ | free-text answer with optional attached chart; separate from `/refine` |
| Guardrails (data-readiness pre-check) | ✅ | `classify_readiness()` avoids costly LLM calls on empty/unreachable sources |
| Real-data chart renderers | ✅ | kpi/area/line/bar/donut/funnel/forecast/table/insight/risk/summary, with clean "no data" states |
| Drag draft → canvas, select/remove/duplicate | ✅ | |
| Citations on canvas widgets | ✅ | "hover to cite" footer |
| Save dashboard + reload (`?dash=`) | ✅ | |
| Live canvas autosave/restore | ✅ | survives refresh / other tabs (`/api/workspace` + localStorage) |
| Change data source | ✅ | sidebar chip / AI-panel re-open |
| Connect-time dataset profiler | ✅ | 5 probe queries → LLM → DatasetProfile (cached 24h) |
| Dual data source: Inflectiv (RAG) + direct PostgreSQL | ✅ | `BaseDataSource` abstraction; `InflectivDataSource` + `DatabaseDataSource` |
| Database schema introspection | ✅ | columns, types, FKs, stats, sample rows; SSRF-safe |
| Light / dark theme | ✅ | persisted |
| Top-bar search | 🟡 | cosmetic input, not wired |

### App shell pages (all auth-gated, real data)
| Page | State | Notes |
|---|---|---|
| Profile | ✅ | name/email/company/role/member-since + AI prefs; edits persist |
| Productivity stats + activity timeline | ✅ | from `/api/stats` + `/api/activity` |
| Datasets | ✅ | real Inflectiv list + health cards (connected/sources/ready) |
| Generated Components | ✅ | real saved components, filters, favorite/delete |
| Saved Insights | ✅ | real grid + honest empty state |
| Team | ✅ | real members, invite/remove, real counts; permissions matrix is a static reference 🟡 |
| Settings (12 sections) | ✅ | load + debounced persist; honest API/integrations/security copy |
| Admin Console | ✅ | real counts, real security events (activity log), honest governance |
| Billing | 🟡 | Free plan + **real usage**; pricing tiers illustrative, **no payments** (by design) |
| Dashboard Library | 🟡 | save/load works (`?dash=`); no dedicated library **grid** in the shell (nav opens builder) |

### Backend / infra
| Feature | State | Notes |
|---|---|---|
| FastAPI app, single service (API + UI) | ✅ | serves `frontend/` via StaticFiles |
| PostgreSQL persistence (JSONB) | ✅ | `init_db()` auto-creates tables, no migrations |
| Redis cache (query + profile) | ✅ | in-memory fallback if Redis absent |
| Inflectiv client (RAG) | ✅ | `top_k≤20` clamp, batch + sequential fallback |
| OpenRouter client (JSON-schema output) | ✅ | fast + strong model split |
| Fireworks AI client (primary, AMD hardware) | ✅ | default provider; OpenRouter is fallback |
| SSE agent bus + poll fallback | ✅ | |
| Dual data-source abstraction (`datasource.py`) | ✅ | `BaseDataSource` → `InflectivDataSource` / `DatabaseDataSource` |
| PostgreSQL direct connector (`db_connector.py`) | ✅ | schema introspection, read-only SQL, SSRF IP validation, DNS-rebinding protection |
| Data-readiness guardrails (`guardrails.py`) | ✅ | classifies sessions before pipeline runs |
| Dataset profiler (`profiler.py`) | ✅ | connect-time LLM profiling, cached 24h |
| Prompt library (`prompts.py`, 9 prompts) | ✅ | planner/structurer/refiner/profiler/chat prompts for both source types |
| `/api/health` (llm/provider/db/cache) | ✅ | returns `{ok, llm, provider, db, cache}` |
| `/api/db/test` + `/api/db/tables` | ✅ | SSRF-safe database connection testing + table listing |
| Auto-persist generated drafts | ✅ | → components + insights + activity |
| No-cache headers on UI assets | ✅ | avoids stale dc-runtime files |
| Vendored React (no CDN) | ✅ | `frontend/vendor/` |
| Unit test file (`tests/test_guardrails.py`, 4 tests) | ✅ | pytest configured |
| E2E smoke test script (`scripts/test_db_smoke.sh`, 271 lines) | ✅ | tests all DB-facing API endpoints |
| Design specs + impl plans (`docs/superpowers/`) | ✅ | chart switching, open-access guardrails, chat |
| CLAUDE.md (AI commit rules) | ✅ | no AI attribution in commit messages |

### Deployment
| Item | State | Notes |
|---|---|---|
| Clean repo structure (frontend/backend/docs) | ✅ | |
| `.gitignore` + `.env.example` (no secrets committed) | ✅ | verified `.env` untracked |
| Railway config (`railway.json`, `Procfile`, root `requirements.txt`, `runtime.txt`) | ✅ | start: `uvicorn main:app --app-dir backend` |
| Deploy-style start verified locally | ✅ | serves API + UI on `$PORT` |
| Actually deployed to Railway | ⬜ | user to deploy (repo pushed to GitHub) |

---

## 2. Test status

### Automated unit tests (`pytest`)
| Test | State | Notes |
|---|---|---|
| `tests/test_guardrails.py` (4 tests) | ✅ | `classify_readiness()` — no session, empty source, negative count, ready |
| Backend integration tests (auth, session, pipeline, CRUD) | ⬜ | `pytest.ini` configured, no tests yet |

### E2E smoke test
| Script | State | Notes |
|---|---|---|
| `scripts/test_db_smoke.sh` (271 lines) | ✅ | Tests all DB-facing endpoints: health → signup → me → generate → refine → chat → dashboards → workspace → edge cases. Requires Docker + running backend. |

### Verified end-to-end (manual)
- ✅ Inflectiv API live: key valid, base URL, `?dataset_id=` scoping, `top_k≤20`, 28 datasets listed.
- ✅ `GET /api/health` → `{ok:true, llm:true, provider:"fireworks", db:true, cache:"redis"}`.
- ✅ Signup (curl + full browser wizard) → account in Postgres → token; login returns token.
- ✅ Onboarding step-3 key capture → 28-dataset dropdown → select → account created.
- ✅ Direct DB connection (onboarding step-3 / connect screen) → table picker → workspace.
- ✅ Dashboard auto-connects from token (no overlay) + shows recommended queries.
- ✅ Generate against "Web3 VC Intelligence Index": real drafts, provenance (6 sources), grounded/estimated, live SSE steps.
- ✅ Guardrails block generate/refine/chat when no session or empty source (returns clean message, no LLM call).
- ✅ Drafts auto-persist → `/api/components` (5), `/api/insights`, `/api/activity`, `/api/stats`.
- ✅ Save dashboard → `/api/dashboards`; reload `?dash=1` restores widgets.
- ✅ Workspace autosave → refresh keeps the canvas (verified round-trip).
- ✅ Chat endpoint (`/api/chat`) returns free-text answer + optional chart with provenance.
- ✅ App pages render real data (Profile/Datasets/Components/Insights/Team/Admin/Billing); identity + nav counts real.
- ✅ Dead-button audit: **0 dead buttons** on Dashboard and App (handler-detection sweep).
- ✅ Fabricated-data sweep: **0 fake strings** (SOC2/HIPAA/Okta/SCIM/Northwind/Avery/agent-v4/INV-2026/Visa) across all tabs + settings subsections.
- ✅ Graceful fallbacks: no LLM key → clean 503; DB down → app still boots; cache falls back to memory.
- ✅ Repo hygiene: `git add --dry-run` confirms `.env`/`.venv` untracked; 37 clean files committed.

### Demo account (local)
`e2e@demo.ai` / `pw123456` (exists in the local docker Postgres).

---

## 3. What's left / TODO

### Functional
- ⬜ **Dashboard Library grid** in the app shell (endpoint `/api/dashboards` exists; needs a UI page).
- ⬜ Real **password-reset email** + token verification.
- ⬜ Real **OAuth SSO** (Google/Microsoft/Slack).
- ⬜ **Payments** (Stripe) if billing is to be functional.
- ⬜ Top-bar **global search** wiring.
- ⬜ **Chart type switching** on live widgets (design spec at `docs/superpowers/specs/2026-07-11-open-access-guardrails-chat-chart-switching-design.md`; not implemented).
- ⬜ **Open-access guardrails** (rate limiting, anonymous usage caps).
- ⬜ Multi-dataset per dashboard; per-widget refresh.
- ⬜ Pagination for components/insights (currently `LIMIT 200`).

### Testing (the main gap)
- ⬜ **Backend integration tests** (pytest): auth, `/session` key-fallback, pipeline plan→retrieve→structure (mock Inflectiv/OpenRouter), CRUD routes, `current_user`.
- ⬜ **Frontend E2E** (Playwright) scripting the manual flows above so they run in CI.
- ⬜ CI workflow (GitHub Actions) running the above on push.

### Hardening (post-hackathon)
- ⬜ Rate limiting, refresh-token rotation / expiry, CSRF.
- ⬜ Structured logging + error monitoring.
- ⬜ Tighten CORS in prod (drop `*`).
- ⬜ Secrets via a manager rather than `.env`.

---

## 4. Known notes for reviewers
- Pricing tiers and the permissions matrix are **illustrative**; billing shows **real usage only** (no payment processing).
- Charts from a large dataset are labelled **estimated** when values can't be grounded in retrieved chunks — this is a deliberate trust feature, not a bug.
- `docs/PROTOTYPE_NOTES.md` describes the original design prototype (historical).
- Design specs for upcoming features (chart switching, open-access guardrails) live in `docs/superpowers/`.
- Direct PostgreSQL connection bypasses the RAG/honesty layer — all values are `exact: true`, confidence is `95`, no provenance sources emitted.

(End of file - total 175 lines)
