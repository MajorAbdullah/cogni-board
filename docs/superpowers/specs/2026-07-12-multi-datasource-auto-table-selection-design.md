# Multi-source data connections with automatic table selection

Status: approved for implementation (dev branch)
Date: 2026-07-12

## Problem

Today a user connects exactly one PostgreSQL table and/or one Inflectiv
dataset, ever — both live as flat columns on `users`
(`db_connection_string`, `db_table_name`, `inflectiv_key`,
`inflectiv_dataset_id`, `inflectiv_dataset_name`). Connecting Postgres is a
two-step wizard: paste a connection string, then pick a table from a
dropdown (`POST /api/db/tables`). That second step assumes the user knows
which table holds what they want — a "CEO revenue rollup" or "CFO expense
ledger" table name means nothing to a layperson looking at a raw schema.

There's also no way to keep more than one connection around. Reconnecting
to a database you used last week means re-typing the full connection
string (password included) from scratch, and the Datasets tab only shows
whatever is currently configured, not history.

## Goals

1. Connecting Postgres becomes one field: paste a connection string, done.
   The app figures out which table (or tables, via JOIN) answers each
   query, per query — no upfront table picker.
2. Users can save multiple data sources (Postgres connections and/or
   Inflectiv datasets) and switch which one is active without re-entering
   credentials.
3. The Datasets tab lists every previously connected source (both kinds),
   shows which is active, and lets the user reconnect to an old one or add
   a new one.
4. Existing single-connection users are migrated forward with zero
   re-entry of credentials.

## Non-goals

- No querying across multiple *saved sources* in one answer (e.g. joining
  a Postgres table with an Inflectiv dataset). One active source at a
  time, same as today's session model — just switchable without
  re-entering credentials.
- No embedding-based retrieval infrastructure. Table shortlisting uses the
  existing chat-completion LLM path (`llm.chat_json`/`chat_text`); no new
  embeddings dependency.
- No schema-change detection, auto-reindexing, or dedicated "reindex"
  action. If a connected database's schema changes, the user deletes and
  re-adds the connection (re-running indexing via the normal add flow) —
  no background polling for drift.
- No workspace/team sharing of saved connections. Same single-owner model
  as the rest of `users`-scoped data today.

## 1. Data model

New table, additive — existing flat columns on `users` stay in place as
the migration source (see §6), not removed:

```sql
CREATE TABLE IF NOT EXISTS data_sources (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type TEXT NOT NULL,              -- 'postgresql' | 'inflectiv'
  label TEXT NOT NULL,             -- display name, user-editable
  secret_enc BYTEA NOT NULL,       -- Fernet-encrypted JSON, see below
  table_index JSONB,               -- postgresql only, see §2
  meta JSONB,                      -- masked/display-safe info, see below
  is_active BOOLEAN NOT NULL DEFAULT false,
  last_connected_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS data_sources_user_idx ON data_sources(user_id);
```

`secret_enc` decrypts to:
- Postgres: `{"conn_string": "postgresql://..."}`
- Inflectiv: `{"key": "...", "dataset_id": 123, "dataset_name": "..."}`

`meta` (safe to send to the browser as-is, no decryption needed):
- Postgres: `{"host_masked": "...", "table_count": 47}`
- Inflectiv: `{"dataset_name": "...", "knowledge_source_count": 12}`

Only one `is_active = true` row per user at a time, enforced in
application code (deactivate the previous active row in the same request
that activates a new one) — matching the rest of this codebase's
demo-grade style of no DB-level cross-row constraints.

### Encryption

New `backend/crypto.py`:

```python
from cryptography.fernet import Fernet
import config

_f = Fernet(config.DATA_SOURCE_ENCRYPTION_KEY.encode())

def encrypt(plaintext: str) -> bytes:
    return _f.encrypt(plaintext.encode())

def decrypt(blob: bytes) -> str:
    return _f.decrypt(bytes(blob)).decode()
```

`config.py` gains `DATA_SOURCE_ENCRYPTION_KEY = os.getenv("DATA_SOURCE_ENCRYPTION_KEY", "")`,
required at startup once this ships (`main.py` fails fast with a clear
message if unset, same pattern as the existing `have_llm()` check).
Generated once via `python -c "from cryptography.fernet import Fernet;
print(Fernet.generate_key().decode())"` and placed in `backend/.env`
(documented in `.env.example`). `cryptography` is added to
`backend/requirements.txt`.

Secrets are decrypted only server-side, only at the moment of connecting
(building a `Session`) — never included in any API response.

## 2. Connect-time indexing (Postgres)

`db_connector.py` gains a lightweight bulk introspection function —
deliberately cheaper than today's `get_table_schema` (which pulls sample
rows and per-column stats), since it runs once per table at connect time
and connect-time DBs may have 20-200+ tables:

```python
def list_tables_light(conn_string: str) -> list[dict]:
    """One table_name + columns(name,type) + FK edges + row estimate per
    table. No sample rows, no per-column stats — those are fetched later,
    only for the handful of tables a given query actually shortlists."""
```

At `POST /api/datasources` (type=postgresql):
1. `test_connection` (existing).
2. `list_tables_light` — all tables, public schema, same scope as today's
   `list_tables`.
3. Batch LLM call(s) generating a one-line business-facing description per
   table from its name + columns (e.g. "monthly recurring revenue by
   account, rolled up for exec reporting"). Batched across tables (e.g.
   ~20 tables per LLM call) to keep call count reasonable at 200 tables.
4. Store as `table_index`:
   ```json
   [{"table_name": "...", "description": "...", "row_estimate": 1200,
     "columns": [{"name": "...", "type": "..."}],
     "foreign_keys": [{"column": "...", "ref_table": "...", "ref_column": "..."}]}]
   ```
5. Progress is streamed the same way agent-reasoning steps already stream
   today (`agentbus` + `/api/agent/stream` SSE, keyed by a `job_id`) —
   reused here for "indexed 34/120 tables" rather than building a second
   streaming mechanism. The endpoint is synchronous from the caller's
   perspective: the HTTP response returns once indexing completes.

## 3. Query-time table selection

`datasource.py`'s `DatabaseDataSource` changes constructor from
`(conn_string, table_name)` to `(conn_string, table_index)`. New internal
step before SQL generation:

```python
async def _shortlist_tables(self, query_intent: str) -> list[str]:
    """One chat_json call: cached table_index (name + description +
    columns + FKs) in, up to ~4 relevant table names out. Expanded with
    any table directly FK-linked to a shortlisted one, so JOINs have
    real neighbors to work with."""
```

The shortlisted tables then get *fully* introspected via today's existing
`db_connector.get_table_schema` (columns, sample rows, stats, FKs) — this
is the expensive call, but now bounded to a handful of tables instead of
all of them. Their combined schema text (all shortlisted tables' real
columns, not just the anchor table's) is passed to the existing
`SQL_WRITER` prompt, which already knows how to write FK-based JOINs —
today it only ever saw one anchor table's columns plus bare FK *target
names* with no column list, so cross-table JOINs partly relied on the LLM
guessing the target table's columns. Passing full schemas for every
shortlisted table closes that gap.

`get_profile()` (seeds suggested KPIs/charts right after connecting) profiles
the 3-5 tables with the highest row estimates / most FK connections from
the index, instead of one fixed table.

`Session` (`sessions.py`) drops `table_name` as a required field for
database mode; it gains `data_source_id: int = 0`. Building a
`DatabaseDataSource` for a session looks up `table_index` from the
`data_sources` row rather than carrying it duplicated in the in-memory
session.

## 4. API

New router, `backend/routes_datasources.py`, mounted at `/api/datasources`,
auth required (`current_user` dependency, matching `routes_app.py`'s
pattern):

- `POST /api/datasources` — add + index a source, mark active (deactivating
  the prior active row). Body: `{type, label?, conn_string}` for postgres
  or `{type: "inflectiv", label?, global_key, dataset_id, dataset_name}`.
  Returns `{id, label, type, meta, session_id}` — a `Session` is created in
  the same call so the frontend can go straight to generating.
- `GET /api/datasources` — list saved sources:
  `[{id, type, label, meta, is_active, last_connected_at}]`.
- `POST /api/datasources/{id}/activate` — decrypt server-side, deactivate
  others, build a fresh `Session`, return `{session_id}`. No credentials
  cross the wire.
- `PATCH /api/datasources/{id}` — `{label}` rename only.
- `DELETE /api/datasources/{id}` — remove a saved source. If it was active,
  no new source is auto-activated (the app returns to the "not connected"
  guardrail state from the existing open-access design, not an error).

`POST /api/db/tables` (today's table-dropdown endpoint) is removed — the
manual table-picker step it served no longer exists in the primary flow.
`POST /api/db/test` stays (still used to validate a connection string
before the full add+index flow). `PATCH /api/me` drops the
`db_connection_string`/`db_table_name`/`inflectiv_*` fields (superseded by
`/api/datasources`); `SignupRequest` keeps them for now since removing a
data source from signup itself is out of scope here.

## 5. Frontend

**Connect flow**: today's two-step wizard (connection string →
`POST /api/db/tables` → table dropdown) collapses to one field — paste
connection string, click Connect, see an indexing progress state ("Analyzing
your database... 34/120 tables"), land in the app once done. The table
dropdown UI is removed.

**Datasets tab** (`/my-datasets`, `Agentic App.dc.html` lines ~215-276,
backed by `GET /api/my-datasets`): the "All datasets" table's row source
changes from "whatever is in the one active config" to
`GET /api/datasources` — every saved source, both kinds, in one list.
Each row shows type, masked connection info, an active/inactive
indicator, a "Set active" action for inactive rows (calls
`POST /api/datasources/{id}/activate`, no credential re-entry), rename,
and delete. An "Add data source" action opens the connect flow described
above for either type.

## 6. Migration of existing users

On `POST /auth/login`, if the authenticating user has no rows in
`data_sources` yet and has legacy config on their `users` row
(`db_connection_string` or `inflectiv_key` set), auto-create the
corresponding `data_sources` row(s) — encrypting the legacy plaintext
connection string, running the same connect-time indexing described in
§2, and marking one active (Postgres preferred if both are present, since
that's the flow with a specific active table already known). This runs
once per user (guarded by the "no rows yet" check) and requires no
separate migration script. Indexing at login adds latency for those
users' first login after this ships — acceptable one-time cost, and it
runs the exact same code path as a fresh connect.

## 7. Error handling

- Invalid/unreachable connection string at `POST /api/datasources`: same
  `DbConnectionFailed`/`DbConnectorError` handling as today's
  `/api/db/test` — generic client-facing message, detail logged
  server-side only (existing pattern in `db_connector.py`).
- Indexing partially fails (LLM call errors for some table batches):
  those tables get a fallback description of just their name + column
  list (no LLM-authored summary) rather than failing the whole connect —
  matches the existing `DatabaseDataSource.get_profile()` fallback
  pattern (`except Exception: profile = DatasetProfile(summary=...)`).
- Shortlist step returns zero tables (e.g. genuinely irrelevant query):
  falls back to the single largest table by row estimate, same as
  picking *a* reasonable default rather than erroring the whole query.
- Activating a deleted/foreign `data_sources.id`: 404, same as existing
  `dashboards/{id}` ownership checks in `routes_app.py`.

## 8. Testing

- `db_connector.list_tables_light`: unit test against a local Postgres
  fixture with a handful of FK-linked tables — asserts column/FK/row-estimate
  shape.
- `DatabaseDataSource._shortlist_tables`: test with a stubbed `chat_json`
  returning a fixed shortlist, asserting FK-neighbor expansion logic.
- `crypto.py`: round-trip encrypt/decrypt test, and a test asserting
  `secret_enc` bytes never appear in any `/api/datasources*` JSON response.
- `POST /api/datasources` → `GET /api/datasources` → `POST
  /api/datasources/{id}/activate` integration test: add a source, confirm
  it's listed and masked correctly, activate it, confirm a working
  `session_id` comes back without the client ever re-sending the
  connection string.
- Migration: a user fixture with legacy `db_connection_string` set logs in
  once, asserts exactly one `data_sources` row appears, active, with the
  original plaintext no longer stored anywhere unencrypted.
