# Multi-source data connections with automatic table selection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user connect PostgreSQL by pasting only a connection string (no manual table picker), have the agent automatically select the right table(s) — including JOINs — per query, and let users save multiple data sources (Postgres + Inflectiv) and switch between them from the Datasets tab without re-entering credentials.

**Architecture:** A new `data_sources` table stores Fernet-encrypted credentials plus a cached `table_index` (per-table LLM-generated descriptions + columns + foreign keys), built once at connect time via lightweight bulk introspection. `DatabaseDataSource` (the existing query-time abstraction) is refactored to pick a per-query table shortlist from that cached index via one LLM call, expand it to FK-linked neighbors, then reuse the existing SQL-writer flow across all shortlisted tables' full schemas. A new `/api/datasources` router handles add/list/activate/rename/delete; activating decrypts server-side and mints a `Session` — no credentials ever cross back to the browser. Existing single-connection users are migrated into the new table automatically on their next login.

**Tech Stack:** FastAPI + Pydantic + psycopg2 (backend), `cryptography` (new dependency, Fernet symmetric encryption), the existing Fireworks/OpenRouter LLM layer (`llm.chat_json`/`chat_text`), custom `dc-runtime` HTML/JS templating for the frontend (no build step, no frontend test framework — verified via Playwright MCP browser automation and the project's existing shell-based smoke-test script).

## Global Constraints

- Never send `secret_enc` (encrypted credential blob) or a raw connection string / Inflectiv key back to the browser in any API response — only masked `meta` fields.
- Exactly one `data_sources` row may have `is_active = true` per user at any time, enforced in application code (deactivate-then-activate in the same call).
- `DATA_SOURCE_ENCRYPTION_KEY` env var is required for any encrypt/decrypt call; missing key raises `crypto.CryptoError`, caught at the call site — never crash the whole app at startup over it (matches this repo's existing lazy `have_llm()` pattern, not a hard fail-fast).
- Table shortlisting uses the existing `llm.chat_json`/`chat_text` chat-completion path only — no new embeddings dependency.
- `db_connector.list_tables_light` must not run per-table `SELECT *`/stats scans (that's what makes today's `get_table_schema` expensive) — only `information_schema` + `pg_class.reltuples` queries, safe at 200+ tables.
- No querying across two different saved sources in one answer — one active source at a time (existing session model), just switchable without re-entering credentials.
- No schema-change detection/auto-reindexing — delete-and-re-add is the only way to refresh a stale index.
- Follow this codebase's existing testing convention: pure-logic code (no live DB/network) gets real `pytest` unit tests with mocked collaborators; code that fundamentally requires a live Postgres connection or a real LLM call is verified via the existing bash smoke-test script (`scripts/test_db_smoke.sh`) and/or Playwright MCP browser automation, exactly as today's `db_connector.py`/`routes_app.py` endpoints already are (zero existing pytest coverage on those, verified only via the smoke script).

---

## File Structure

Backend (new):
- `backend/crypto.py` — Fernet encrypt/decrypt for saved credentials.
- `backend/datasources_store.py` — CRUD over the `data_sources` table (create/list/get/activate/rename/delete), encryption at the boundary.
- `backend/table_indexer.py` — batches `list_tables_light()` output through the LLM to attach one-line descriptions, building the cached `table_index`.
- `backend/routes_datasources.py` — `/api/datasources` router (add/list/activate/rename/delete).
- `backend/tests/test_crypto.py`, `test_datasources_store.py`, `test_table_indexer.py`, `test_datasource_shortlist.py`, `test_sessions.py`, `test_routes_app_migration.py` — new unit tests.

Backend (modified):
- `backend/config.py` — add `DATA_SOURCE_ENCRYPTION_KEY`.
- `backend/requirements.txt` — add `cryptography`.
- `backend/db.py` — add `data_sources` table to `SCHEMA`/`init_db`/`reset_db`.
- `backend/db_connector.py` — add `list_tables_light` + pure `_assemble_table_index` helper.
- `backend/schemas.py` — add `TableDescriptionBatch`, `TableShortlist`, `DataSourceCreate`, `DataSourceRename`; trim `ProfileUpdate`. (Per-table column/FK shape stays plain `dict` throughout — `list_tables_light`/`table_index` are never round-tripped through `chat_json`, so no dedicated Pydantic model is needed for them.)
- `backend/prompts.py` — add `TABLE_DESCRIBER`, `TABLE_SHORTLISTER`.
- `backend/datasource.py` — `DatabaseDataSource` refactor: `(conn_string, table_index)` constructor, `_shortlist_tables`, multi-table `query()`/`get_profile()`.
- `backend/sessions.py` — `Session.table_name` → `table_index` + `data_source_id`.
- `backend/main.py` — mount the new router, drop `/api/db/tables`, update `/api/session`'s database branch for auto table selection, soft startup warning for the encryption key.
- `backend/routes_app.py` — auto-migrate legacy config on login, trim `PATCH /me`.
- `backend/auth.py` — `_mask_conn_string` → `mask_conn_string` (now used cross-module).
- `backend/.env.example` — document `DATA_SOURCE_ENCRYPTION_KEY`.

Frontend (modified):
- `frontend/Agentic Auth.dc.html` — signup wizard's DB step collapses to one field.
- `frontend/Agentic App.dc.html` — Datasets tab lists every saved source (both types), with activate/rename/delete and an "Add data source" panel.

Scripts (modified):
- `scripts/test_db_smoke.sh` — fix the now-invalid "empty table_name rejected" assertion, add coverage for the new `/api/datasources` endpoints.

No new frontend files — this `dc-runtime` app is single-file-per-page by convention.

---

### Task 1: Credential encryption (`backend/crypto.py`)

**Files:**
- Create: `backend/crypto.py`
- Modify: `backend/config.py`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_crypto.py`

**Interfaces:**
- Produces: `crypto.encrypt(plaintext: str) -> bytes`, `crypto.decrypt(blob: bytes) -> str`, `crypto.CryptoError` — consumed by Task 2 (`datasources_store.py`).

- [ ] **Step 1: Add the dependency and install it**

Append to `backend/requirements.txt`:
```
cryptography==44.0.0
```

Run: `cd backend && pip install -r requirements.txt` (or `.venv/bin/pip install -r requirements.txt` if using the existing venv)
Expected: `Successfully installed cryptography-44.0.0 ...`

- [ ] **Step 2: Add the config var**

In `backend/config.py`, after the `ALLOW_PRIVATE_DB_HOSTS` block (before `# --- Redis cache ---`), add:

```python
# --- Saved data-source credential encryption ---
# Encrypts connection strings / API keys stored in the data_sources table.
# Generate once: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
DATA_SOURCE_ENCRYPTION_KEY = os.getenv("DATA_SOURCE_ENCRYPTION_KEY", "")
```

- [ ] **Step 3: Write the failing test**

Create `backend/tests/test_crypto.py`:

```python
import pytest
from cryptography.fernet import Fernet

import config
import crypto


def test_encrypt_decrypt_round_trip(monkeypatch):
    monkeypatch.setattr(config, "DATA_SOURCE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    blob = crypto.encrypt("postgresql://user:pass@host:5432/db")
    assert isinstance(blob, bytes)
    assert b"pass" not in blob
    assert crypto.decrypt(blob) == "postgresql://user:pass@host:5432/db"


def test_missing_key_raises(monkeypatch):
    monkeypatch.setattr(config, "DATA_SOURCE_ENCRYPTION_KEY", "")
    with pytest.raises(crypto.CryptoError):
        crypto.encrypt("secret")


def test_wrong_key_raises_on_decrypt(monkeypatch):
    monkeypatch.setattr(config, "DATA_SOURCE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    blob = crypto.encrypt("secret")
    monkeypatch.setattr(config, "DATA_SOURCE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(blob)
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_crypto.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'crypto'`

- [ ] **Step 5: Implement `backend/crypto.py`**

```python
"""Symmetric encryption for saved data-source credentials.

Connection strings and Inflectiv keys are decrypted only server-side, only
at the moment of connecting — never sent to the browser.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

import config


class CryptoError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = config.DATA_SOURCE_ENCRYPTION_KEY
    if not key:
        raise CryptoError(
            "DATA_SOURCE_ENCRYPTION_KEY is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    try:
        return Fernet(key.encode())
    except ValueError as e:
        raise CryptoError(f"DATA_SOURCE_ENCRYPTION_KEY is not a valid Fernet key: {e}") from e


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt(blob: bytes) -> str:
    try:
        return _fernet().decrypt(bytes(blob)).decode()
    except InvalidToken as e:
        raise CryptoError("Could not decrypt stored credential — wrong key or corrupted data.") from e
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_crypto.py -v`
Expected: `3 passed`

- [ ] **Step 7: Add to `.env.example` and commit**

In `backend/.env.example`, after the `ALLOW_PRIVATE_DB_HOSTS` block, add:

```
# --- Saved data-source credential encryption ---
# Required for the Datasets tab's "save & switch sources without re-entering
# credentials" feature. Generate once and keep it stable — rotating it makes
# every previously saved connection undecryptable.
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
DATA_SOURCE_ENCRYPTION_KEY=
```

```bash
git add backend/crypto.py backend/config.py backend/requirements.txt backend/.env.example backend/tests/test_crypto.py
git commit -m "Add Fernet-based encryption for saved data-source credentials"
```

---

### Task 2: `data_sources` table + persistence (`backend/datasources_store.py`)

**Files:**
- Modify: `backend/db.py`
- Create: `backend/datasources_store.py`
- Test: `backend/tests/test_datasources_store.py`

**Interfaces:**
- Consumes: `crypto.encrypt(str) -> bytes`, `crypto.decrypt(bytes) -> str` (Task 1); `db.execute`, `db.query`, `db.one`, `db.Json` (existing).
- Produces: `store.create(user_id, type_, label, secret: dict, meta: dict, table_index: Optional[list]) -> dict`, `store.list_for_user(user_id) -> list[dict]`, `store.get(id_, user_id) -> Optional[dict]`, `store.decrypt_secret(row: dict) -> dict`, `store.activate(id_, user_id) -> dict` (raises `LookupError` if missing), `store.rename(id_, user_id, label) -> dict` (raises `LookupError`), `store.delete(id_, user_id) -> None` — consumed by Tasks 7 and 8.

- [ ] **Step 1: Add the table to the schema**

In `backend/db.py`, add to the `SCHEMA` string, after the `api_keys` table definition (before the closing `"""`):

```python
CREATE TABLE IF NOT EXISTS data_sources (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  label TEXT NOT NULL,
  secret_enc BYTEA NOT NULL,
  table_index JSONB,
  meta JSONB,
  is_active BOOLEAN NOT NULL DEFAULT false,
  last_connected_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

In `init_db()`, after the existing `ALTER TABLE ... db_table_name` line, add:

```python
        c.execute("CREATE INDEX IF NOT EXISTS data_sources_user_idx ON data_sources(user_id);")
```

In `reset_db()`, change the DROP TABLE statement to also drop the new table:

```python
        c.execute(
            "DROP TABLE IF EXISTS users, dashboards, saved_components, saved_insights, "
            "activity_log, team_members, api_keys, data_sources CASCADE;"
        )
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_datasources_store.py`:

```python
import json

import datasources_store as store


def test_create_deactivates_others_and_encrypts_secret(monkeypatch):
    calls = []

    def fake_execute(sql, params=()):
        calls.append((sql, params))
        if "RETURNING *" in sql:
            return {"id": 7, "user_id": 1, "type": "postgresql", "label": "My DB",
                     "secret_enc": params[3], "table_index": None, "meta": params[5],
                     "is_active": True}
        return None

    monkeypatch.setattr(store.db, "execute", fake_execute)
    monkeypatch.setattr(store.crypto, "encrypt", lambda s: b"ENC:" + s.encode())

    row = store.create(1, "postgresql", "My DB", {"conn_string": "postgresql://x"}, {"host_masked": "***"})

    assert row["id"] == 7
    assert calls[0][0].startswith("UPDATE data_sources SET is_active=false")
    assert calls[0][1] == (1,)
    assert calls[1][1][3] == b'ENC:{"conn_string": "postgresql://x"}'


def test_activate_raises_for_missing_row(monkeypatch):
    monkeypatch.setattr(store.db, "one", lambda sql, params: None)
    try:
        store.activate(99, 1)
        assert False, "expected LookupError"
    except LookupError:
        pass


def test_rename_raises_for_missing_row(monkeypatch):
    monkeypatch.setattr(store.db, "execute", lambda sql, params: None)
    try:
        store.rename(99, 1, "New label")
        assert False, "expected LookupError"
    except LookupError:
        pass


def test_decrypt_secret_round_trip(monkeypatch):
    monkeypatch.setattr(store.crypto, "decrypt", lambda b: b.decode()[4:])
    row = {"secret_enc": b"ENC:" + json.dumps({"conn_string": "postgresql://x"}).encode()}
    assert store.decrypt_secret(row) == {"conn_string": "postgresql://x"}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_datasources_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'datasources_store'`

- [ ] **Step 4: Implement `backend/datasources_store.py`**

```python
"""Persistence + encryption for saved data sources. CRUD over the
data_sources table; secrets are Fernet-encrypted at rest via crypto.py and
decrypted only server-side, only when actually connecting.
"""
from __future__ import annotations

import json
from typing import Optional

import crypto
import db


def create(user_id: int, type_: str, label: str, secret: dict,
           meta: dict, table_index: Optional[list] = None) -> dict:
    """Insert a new source and mark it the user's sole active one."""
    db.execute("UPDATE data_sources SET is_active=false WHERE user_id=%s", (user_id,))
    row = db.execute(
        """INSERT INTO data_sources (user_id,type,label,secret_enc,table_index,meta,is_active,last_connected_at)
           VALUES (%s,%s,%s,%s,%s,%s,true,now()) RETURNING *""",
        (user_id, type_, label, crypto.encrypt(json.dumps(secret)),
         db.Json(table_index) if table_index is not None else None, db.Json(meta)),
    )
    return row


def list_for_user(user_id: int) -> list[dict]:
    return db.query(
        "SELECT id,type,label,meta,is_active,last_connected_at,created_at "
        "FROM data_sources WHERE user_id=%s ORDER BY is_active DESC, last_connected_at DESC NULLS LAST",
        (user_id,),
    )


def get(id_: int, user_id: int) -> Optional[dict]:
    return db.one("SELECT * FROM data_sources WHERE id=%s AND user_id=%s", (id_, user_id))


def decrypt_secret(row: dict) -> dict:
    return json.loads(crypto.decrypt(row["secret_enc"]))


def activate(id_: int, user_id: int) -> dict:
    row = get(id_, user_id)
    if not row:
        raise LookupError("data source not found")
    db.execute("UPDATE data_sources SET is_active=false WHERE user_id=%s", (user_id,))
    db.execute("UPDATE data_sources SET is_active=true, last_connected_at=now() WHERE id=%s", (id_,))
    row["is_active"] = True
    return row


def rename(id_: int, user_id: int, label: str) -> dict:
    row = db.execute(
        "UPDATE data_sources SET label=%s WHERE id=%s AND user_id=%s RETURNING id,type,label,meta,is_active",
        (label, id_, user_id),
    )
    if not row:
        raise LookupError("data source not found")
    return row


def delete(id_: int, user_id: int) -> None:
    db.execute("DELETE FROM data_sources WHERE id=%s AND user_id=%s", (id_, user_id))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_datasources_store.py -v`
Expected: `4 passed`

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/datasources_store.py backend/tests/test_datasources_store.py
git commit -m "Add data_sources table and CRUD store for saved connections"
```

---

### Task 3: Lightweight bulk table introspection (`db_connector.list_tables_light`)

**Files:**
- Modify: `backend/db_connector.py`
- Test: `backend/tests/test_db_connector_light.py`

**Interfaces:**
- Produces: `db_connector.list_tables_light(conn_string: str) -> list[dict]` — each item shaped `{table_name, row_estimate, columns: [{name,type}], foreign_keys: [{column,ref_table,ref_column}]}`; `db_connector._assemble_table_index(col_rows, fk_rows) -> list[dict]` (pure, unit-tested). Consumed by Tasks 4, 7, 8.

- [ ] **Step 1: Write the failing test (pure assembly logic — no live DB)**

Create `backend/tests/test_db_connector_light.py`:

```python
from db_connector import _assemble_table_index


def test_assemble_table_index_groups_columns_and_fks():
    col_rows = [
        {"table_name": "orders", "column_name": "id", "data_type": "integer", "row_estimate": 100},
        {"table_name": "orders", "column_name": "customer_id", "data_type": "integer", "row_estimate": 100},
        {"table_name": "customers", "column_name": "id", "data_type": "integer", "row_estimate": 20},
    ]
    fk_rows = [
        {"table_name": "orders", "column_name": "customer_id", "ref_table": "customers", "ref_column": "id"},
    ]
    result = _assemble_table_index(col_rows, fk_rows)

    assert [t["table_name"] for t in result] == ["customers", "orders"]
    orders = result[1]
    assert orders["row_estimate"] == 100
    assert orders["columns"] == [{"name": "id", "type": "integer"}, {"name": "customer_id", "type": "integer"}]
    assert orders["foreign_keys"] == [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}]
    assert result[0]["foreign_keys"] == []


def test_assemble_table_index_handles_null_row_estimate():
    col_rows = [{"table_name": "t", "column_name": "id", "data_type": "integer", "row_estimate": None}]
    result = _assemble_table_index(col_rows, [])
    assert result[0]["row_estimate"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_db_connector_light.py -v`
Expected: FAIL — `ImportError: cannot import name '_assemble_table_index'`

- [ ] **Step 3: Implement in `backend/db_connector.py`**

Add after `list_tables()` (which stays unchanged and unused by the new flow, but is left in place — nothing else references removing it):

```python
def list_tables_light(conn_string: str) -> list[dict]:
    """One row per (table, column) plus FK edges and row estimates — assembled
    into a per-table structure. No sample rows, no per-column stats (those are
    fetched later, only for the handful of tables a given query actually
    shortlists) — this runs once per table at connect time and a connected
    database may have 20-200+ tables."""
    conn = _connect(conn_string)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute(
                """SELECT c.table_name, c.column_name, c.data_type,
                          (SELECT reltuples::bigint FROM pg_class
                           WHERE oid::regclass::text = c.table_name) AS row_estimate
                   FROM information_schema.columns c
                   JOIN information_schema.tables t
                     ON t.table_schema = c.table_schema AND t.table_name = c.table_name
                   WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
                   ORDER BY c.table_name, c.ordinal_position"""
            )
            col_rows = [dict(r) for r in c.fetchall()]

            c.execute(
                """SELECT tc.table_name, kcu.column_name,
                          ccu.table_name AS ref_table, ccu.column_name AS ref_column
                   FROM information_schema.table_constraints tc
                   JOIN information_schema.key_column_usage kcu
                     ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
                   JOIN information_schema.constraint_column_usage ccu
                     ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
                   WHERE tc.table_schema = 'public' AND tc.constraint_type = 'FOREIGN KEY'"""
            )
            fk_rows = [dict(r) for r in c.fetchall()]
    finally:
        conn.close()
    return _assemble_table_index(col_rows, fk_rows)


def _assemble_table_index(col_rows: list[dict], fk_rows: list[dict]) -> list[dict]:
    """Pure assembly step, split out so it's unit-testable without a live DB."""
    tables: dict[str, dict] = {}
    for r in col_rows:
        t = tables.setdefault(r["table_name"], {
            "table_name": r["table_name"], "row_estimate": r["row_estimate"] or 0,
            "columns": [], "foreign_keys": [],
        })
        t["columns"].append({"name": r["column_name"], "type": r["data_type"]})
    for r in fk_rows:
        t = tables.get(r["table_name"])
        if t:
            t["foreign_keys"].append(
                {"column": r["column_name"], "ref_table": r["ref_table"], "ref_column": r["ref_column"]}
            )
    return sorted(tables.values(), key=lambda t: t["table_name"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_db_connector_light.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/db_connector.py backend/tests/test_db_connector_light.py
git commit -m "Add lightweight bulk table introspection for connect-time indexing"
```

---

### Task 4: LLM table-description batching (`backend/table_indexer.py`)

**Files:**
- Modify: `backend/schemas.py`
- Modify: `backend/prompts.py`
- Create: `backend/table_indexer.py`
- Test: `backend/tests/test_table_indexer.py`

**Interfaces:**
- Consumes: `db_connector.list_tables_light()` output shape (Task 3); `llm.chat_json(system, user, model_cls) -> model_cls instance` (existing).
- Produces: `table_indexer.build_table_index(light_tables: list[dict]) -> list[dict]` (async) — same shape as input with `description: str` filled in per table. Consumed by Tasks 7 and 8.

- [ ] **Step 1: Add schemas**

In `backend/schemas.py`, add near the other structured-output models (after `DatasetProfile`):

```python
class TableDescriptionBatch(BaseModel):
    model_config = {"extra": "ignore"}
    descriptions: dict[str, str] = Field(default_factory=dict)  # table_name -> one-line description


class TableShortlist(BaseModel):
    model_config = {"extra": "ignore"}
    tables: list[str] = Field(default_factory=list)
```

- [ ] **Step 2: Add prompts**

In `backend/prompts.py`, add after `DB_PROFILER`:

```python
TABLE_DESCRIBER = """You write one-line business-facing descriptions for PostgreSQL
tables, given only their name and column list. Describe what the table is likely used
for in plain language (e.g. "monthly recurring revenue by account, rolled up for exec
reporting"), not a restatement of the column names. Keep each description under 15 words.

Return a JSON object mapping each table_name to its description. Cover every table given
to you — do not skip any.
"""

TABLE_SHORTLISTER = """You pick which database tables are relevant to a user's question,
given a catalog of tables (name, description, columns, row estimate). Rules:
- Return up to 4 table names, most relevant first.
- Prefer tables whose description or columns most directly match the question's subject.
- If the question implies a relationship (e.g. "which customers bought the most"),
  include both sides of that relationship even if only one directly matches by name.
- If nothing in the catalog is plausibly relevant, return the table with the largest
  row_estimate as a fallback.
"""
```

- [ ] **Step 3: Write the failing test**

Create `backend/tests/test_table_indexer.py`:

```python
import asyncio

import table_indexer
from schemas import TableDescriptionBatch


def test_build_table_index_batches_and_fills_descriptions(monkeypatch):
    calls = []

    async def fake_chat_json(system, user, model_cls, **kwargs):
        calls.append(user)
        names = [line.split(":")[0][2:] for line in user.splitlines()]
        return TableDescriptionBatch(descriptions={n: f"desc for {n}" for n in names})

    monkeypatch.setattr(table_indexer, "chat_json", fake_chat_json)

    light_tables = [
        {"table_name": f"t{i}", "row_estimate": i, "columns": [{"name": "id", "type": "integer"}], "foreign_keys": []}
        for i in range(45)
    ]
    result = asyncio.run(table_indexer.build_table_index(light_tables))

    assert len(calls) == 3  # 45 tables / batch size 20 -> 3 batches
    assert result[0]["description"] == "desc for t0"
    assert result[44]["description"] == "desc for t44"


def test_build_table_index_falls_back_on_llm_error(monkeypatch):
    async def failing_chat_json(*a, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(table_indexer, "chat_json", failing_chat_json)
    light_tables = [{"table_name": "t0", "row_estimate": 5, "columns": [], "foreign_keys": []}]
    result = asyncio.run(table_indexer.build_table_index(light_tables))
    assert result[0]["description"] == ""
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_table_indexer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'table_indexer'`

- [ ] **Step 5: Implement `backend/table_indexer.py`**

```python
"""Batch LLM description generation for a database's table catalog — used once
at connect time to build the cached table_index."""
from __future__ import annotations

import prompts
from llm import chat_json
from schemas import TableDescriptionBatch

BATCH_SIZE = 20


async def build_table_index(light_tables: list[dict]) -> list[dict]:
    """light_tables: db_connector.list_tables_light() output. Returns the same
    shape with a `description` filled in per table, generated in batches of
    BATCH_SIZE to bound prompt size at 200+ tables. Falls back to an empty
    description (name + columns only) for any batch whose LLM call fails,
    rather than failing the whole connect."""
    indexed = [dict(t) for t in light_tables]
    for start in range(0, len(indexed), BATCH_SIZE):
        batch = indexed[start:start + BATCH_SIZE]
        catalog = "\n".join(
            f"- {t['table_name']}: columns=[{', '.join(c['name'] for c in t['columns'])}]"
            for t in batch
        )
        try:
            result = await chat_json(prompts.TABLE_DESCRIBER, catalog, TableDescriptionBatch)
            for t in batch:
                t["description"] = result.descriptions.get(t["table_name"], "")
        except Exception:
            for t in batch:
                t["description"] = ""
    return indexed
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_table_indexer.py -v`
Expected: `2 passed`

- [ ] **Step 7: Commit**

```bash
git add backend/schemas.py backend/prompts.py backend/table_indexer.py backend/tests/test_table_indexer.py
git commit -m "Add batched LLM table-description generation for connect-time indexing"
```

---

### Task 5: Query-time table shortlisting (`backend/datasource.py`)

**Files:**
- Modify: `backend/datasource.py`
- Test: `backend/tests/test_datasource_shortlist.py`

**Interfaces:**
- Consumes: `table_index` shape from Task 4 (`{table_name, description, row_estimate, columns, foreign_keys}`); `db_connector.get_table_schema(conn_string, table_name) -> dict` (existing, unchanged); `TableShortlist` schema (Task 4).
- Produces: `DatabaseDataSource(conn_string: str, table_index: list[dict])` — new constructor signature (replaces `(conn_string, table_name)`); `DatabaseDataSource._shortlist_tables(query_intent: str) -> list[str]` (async). Consumed by Tasks 6, 7, 8.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_datasource_shortlist.py`:

```python
import asyncio

import datasource
from schemas import TableShortlist


def _table_index():
    return [
        {"table_name": "orders", "row_estimate": 500,
         "columns": [{"name": "id", "type": "integer"}, {"name": "customer_id", "type": "integer"}],
         "foreign_keys": [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}],
         "description": "customer orders"},
        {"table_name": "customers", "row_estimate": 50,
         "columns": [{"name": "id", "type": "integer"}, {"name": "name", "type": "text"}],
         "foreign_keys": [], "description": "customer records"},
        {"table_name": "products", "row_estimate": 10,
         "columns": [{"name": "id", "type": "integer"}], "foreign_keys": [], "description": "catalog"},
    ]


def test_shortlist_expands_fk_neighbors(monkeypatch):
    async def fake_chat_json(system, user, model_cls, **kwargs):
        return TableShortlist(tables=["orders"])

    monkeypatch.setattr(datasource, "chat_json", fake_chat_json)
    ds = datasource.DatabaseDataSource("postgresql://x", _table_index())
    names = asyncio.run(ds._shortlist_tables("total orders per customer"))
    assert set(names) == {"orders", "customers"}


def test_shortlist_falls_back_to_largest_table_on_empty_result(monkeypatch):
    async def fake_chat_json(system, user, model_cls, **kwargs):
        return TableShortlist(tables=[])

    monkeypatch.setattr(datasource, "chat_json", fake_chat_json)
    ds = datasource.DatabaseDataSource("postgresql://x", _table_index())
    names = asyncio.run(ds._shortlist_tables("irrelevant question"))
    assert names == ["orders"]  # largest row_estimate


def test_shortlist_falls_back_to_largest_table_on_llm_error(monkeypatch):
    async def failing_chat_json(*a, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(datasource, "chat_json", failing_chat_json)
    ds = datasource.DatabaseDataSource("postgresql://x", _table_index())
    names = asyncio.run(ds._shortlist_tables("anything"))
    assert names == ["orders"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_datasource_shortlist.py -v`
Expected: FAIL — `TypeError: DatabaseDataSource.__init__() missing ...` (constructor still takes `table_name`, not `table_index`)

- [ ] **Step 3: Refactor `DatabaseDataSource` in `backend/datasource.py`**

Replace the entire `DatabaseDataSource` class (currently lines 78–189) with:

```python
class DatabaseDataSource(BaseDataSource):
    def __init__(self, conn_string: str, table_index: list[dict]):
        self._conn_string = conn_string
        self._table_index = table_index
        self._schema_cache: dict[str, dict] = {}
        self._profile: Optional[DatasetProfile] = None

    async def list_collections(self) -> list[dict]:
        return [{"id": t["table_name"], "name": t["table_name"],
                 "row_estimate": t.get("row_estimate", 0)} for t in self._table_index]

    async def _shortlist_tables(self, query_intent: str) -> list[str]:
        """Cached table_index (name + description + columns + FKs) in, up to
        ~4 relevant table names out — expanded with any table directly
        FK-linked to a shortlisted one so JOINs have real neighbors."""
        if not self._table_index:
            return []
        catalog = "\n".join(
            f"- {t['table_name']} (~{t.get('row_estimate', 0)} rows): {t.get('description', '')} "
            f"columns=[{', '.join(c['name'] for c in t.get('columns', []))}]"
            for t in self._table_index
        )
        try:
            result = await chat_json(prompts.TABLE_SHORTLISTER,
                                      f"Catalog:\n{catalog}\n\nQuestion: {query_intent}", TableShortlist)
            valid = {t["table_name"] for t in self._table_index}
            names = [n for n in result.tables if n in valid]
        except Exception:
            names = []
        if not names:
            largest = max(self._table_index, key=lambda t: t.get("row_estimate", 0))
            names = [largest["table_name"]]
        by_name = {t["table_name"]: t for t in self._table_index}
        expanded = set(names)
        for n in list(names):
            for fk in by_name.get(n, {}).get("foreign_keys", []):
                if fk["ref_table"] in by_name:
                    expanded.add(fk["ref_table"])
        return list(expanded)

    def _full_schema_for(self, table_names: list[str]) -> dict[str, dict]:
        for name in table_names:
            if name not in self._schema_cache:
                self._schema_cache[name] = db_connector.get_table_schema(self._conn_string, name)
        return {name: self._schema_cache[name] for name in table_names}

    async def get_profile(self, _collection_id: str = "", emit: Optional[Callable] = None) -> DatasetProfile:
        if self._profile:
            return self._profile

        async def _emit(t, s="run"):
            if emit:
                await emit(t, s)

        await _emit("Profiling database")
        seed = sorted(self._table_index,
                      key=lambda t: (len(t.get("foreign_keys", [])), t.get("row_estimate", 0)),
                      reverse=True)[:3]
        schemas = self._full_schema_for([t["table_name"] for t in seed])

        sections = []
        for name, schema in schemas.items():
            col_lines = [f"  - {c['column_name']} ({c['data_type']})" for c in schema.get("columns", [])]
            sample_lines = [f"  {row}" for row in schema.get("sample_rows", [])[:5]]
            sections.append(
                f"Table: {name}\nRow count: {schema.get('row_count', 0)}\n"
                f"Columns:\n" + "\n".join(col_lines) + "\n\nSample rows:\n" + "\n".join(sample_lines)
            )
        user = "\n\n".join(sections)
        try:
            profile = await chat_json(prompts.DB_PROFILER, user, DatasetProfile)
        except Exception:
            profile = DatasetProfile(summary=f"PostgreSQL database ({len(self._table_index)} tables)")

        total_rows = sum(s.get("row_count", 0) for s in schemas.values())
        profile.size_estimate = "small" if total_rows <= 10000 else "large"
        self._profile = profile
        await _emit("Profile ready", "done")
        return profile

    async def query(self, _collection_id: str, queries: list[str], top_k: int,
                    emit: Optional[Callable] = None) -> dict[str, list[dict]]:
        async def _emit(t, s="run"):
            if emit:
                await emit(t, s)

        out: dict[str, list[dict]] = {}
        for q in queries:
            await _emit(f"Selecting table(s) for: {q}")
            table_names = await self._shortlist_tables(q)
            schemas = self._full_schema_for(table_names)

            schema_text = ""
            for name, schema in schemas.items():
                schema_text += f"\nTable: {name}\nColumns:\n"
                for col in schema.get("columns", []):
                    schema_text += f"  - {name}.{col['column_name']} ({col['data_type']})\n"
                fks = schema.get("foreign_keys", [])
                if fks:
                    schema_text += "Foreign keys:\n"
                    for fk in fks:
                        schema_text += f"  {name}.{fk['column_name']} -> {fk['ref_table']}({fk['ref_column']})\n"
                schema_text += f"Row count: {schema.get('row_count', 0)}\n"

            await _emit(f"Querying database: {q}")
            user = (
                f"Schema:\n{schema_text}\n\n"
                f"Query intent: {q}\n\n"
                f"Write a single PostgreSQL SELECT statement to answer this query. "
                f"Use column names from the schema, qualified with table name where more "
                f"than one table is involved. JOIN across the given tables via their foreign "
                f"keys to get human-readable labels instead of raw IDs wherever possible. "
                f"Include a LIMIT {top_k}. Return ONLY the SQL, no explanation."
            )
            try:
                sql = await chat_text(prompts.SQL_WRITER, user, temperature=0.1)
                sql = sql.strip()
                if sql.startswith("```"):
                    sql = sql.split("```", 2)[1]
                    if sql.lstrip().startswith("sql"):
                        sql = sql.lstrip()[3:]
                    sql = sql.strip()
                rows = db_connector.execute_readonly(self._conn_string, sql)
                out[q] = [{"text": json.dumps(r, default=str), "score": 1.0, "knowledge_source_id": 0, "chunk_index": i}
                          for i, r in enumerate(rows)]
                await _emit(f"  ↳ {q} — {len(rows)} rows returned ({', '.join(table_names)})")
            except Exception as e:
                out[q] = []
                await _emit(f"  ↳ {q} — error: {e}")
        return out

    @property
    def source_name(self) -> str:
        return f"{len(self._table_index)} tables" if self._table_index else "database"

    @property
    def size_estimate(self) -> str:
        return "small"

    @property
    def row_count(self) -> int:
        return sum(t.get("row_estimate", 0) for t in self._table_index)
```

Update the import line at the top of `backend/datasource.py`:

```python
from schemas import DatasetProfile, TableShortlist
```

Update `make_datasource` at the bottom of the file:

```python
def make_datasource(session) -> BaseDataSource:
    if session.source_type == "database" and session.conn_string:
        return DatabaseDataSource(session.conn_string, session.table_index)
    client = InflectivClient(session.global_key)
    return InflectivDataSource(client, session.dataset_id, session.dataset_name,
                               session.knowledge_source_count)
```

(`session.table_index` doesn't exist yet — Task 6 adds it. This is expected to reference a not-yet-existing attribute; Task 6 immediately follows.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_datasource_shortlist.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/datasource.py backend/tests/test_datasource_shortlist.py
git commit -m "Refactor DatabaseDataSource for per-query table shortlisting and multi-table JOINs"
```

---

### Task 6: `Session` gains `table_index` (`backend/sessions.py`)

**Files:**
- Modify: `backend/sessions.py`
- Test: `backend/tests/test_sessions.py`

**Interfaces:**
- Produces: `sessions.create(global_key="", dataset=None, source_type="inflectiv", conn_string="", table_index=None, dataset_name="", data_source_id=0) -> Session`; `Session.table_index: list`, `Session.data_source_id: int`. Consumed by Tasks 7 and 8. (`Session.table_name` is removed — Task 5's `make_datasource` already expects `session.table_index`.)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_sessions.py`:

```python
import sessions


def test_create_database_session_computes_record_count_from_table_index():
    table_index = [{"table_name": "orders", "row_estimate": 500}, {"table_name": "customers", "row_estimate": 50}]
    s = sessions.create(source_type="database", conn_string="postgresql://x",
                         table_index=table_index, dataset_name="my-db", data_source_id=3)
    assert s.source_type == "database"
    assert s.table_index == table_index
    assert s.data_source_id == 3
    assert s.record_count == 550
    assert s.dataset_name == "my-db"
    assert sessions.get(s.session_id) is s


def test_create_inflectiv_session_unaffected_by_table_index_default():
    s = sessions.create(global_key="k", dataset={"id": 1, "name": "ds", "knowledge_source_count": 12})
    assert s.table_index == []
    assert s.record_count == 12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_sessions.py -v`
Expected: FAIL — `TypeError: create() got an unexpected keyword argument 'table_index'`

- [ ] **Step 3: Update `backend/sessions.py`**

Replace the full file contents:

```python
"""In-memory session store: session_id -> {global_key, dataset, profile}.

Keeps the user's Inflectiv global key server-side after the Connect screen. The
browser only ever holds the opaque session_id.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Optional

from schemas import DatasetProfile


@dataclass
class Session:
    session_id: str
    global_key: str = ""
    dataset_id: int = 0
    dataset_name: str = ""
    profile: Optional[DatasetProfile] = None
    knowledge_source_count: int = 0
    source_type: str = "inflectiv"
    conn_string: str = ""
    table_index: list = field(default_factory=list)
    data_source_id: int = 0
    record_count: int = 0


_sessions: dict[str, Session] = {}


def create(global_key: str = "", dataset: Optional[dict] = None,
           source_type: str = "inflectiv", conn_string: str = "",
           table_index: Optional[list] = None, dataset_name: str = "",
           data_source_id: int = 0) -> Session:
    sid = "sess_" + secrets.token_urlsafe(16)
    ks_count = dataset.get("knowledge_source_count", 0) if dataset else 0
    table_index = table_index or []
    total_rows = sum(t.get("row_estimate", 0) for t in table_index)
    s = Session(
        session_id=sid,
        global_key=global_key,
        dataset_id=dataset["id"] if dataset else 0,
        dataset_name=dataset.get("name", "") if dataset else dataset_name,
        knowledge_source_count=ks_count,
        source_type=source_type,
        conn_string=conn_string,
        table_index=table_index,
        data_source_id=data_source_id,
        record_count=ks_count if source_type == "inflectiv" else total_rows,
    )
    _sessions[sid] = s
    return s


def get(session_id: str) -> Optional[Session]:
    return _sessions.get(session_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_sessions.py tests/test_guardrails.py -v`
Expected: `6 passed` (2 new + 4 existing guardrail tests, confirming the `Session(session_id=..., record_count=...)` kwargs-only construction they use still works with the new field)

- [ ] **Step 5: Commit**

```bash
git add backend/sessions.py backend/tests/test_sessions.py
git commit -m "Replace Session.table_name with table_index for auto table selection"
```

---

### Task 7: `/api/datasources` router + wire into `main.py`

**Files:**
- Modify: `backend/auth.py`
- Modify: `backend/schemas.py`
- Create: `backend/routes_datasources.py`
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `datasources_store` (Task 2), `table_indexer.build_table_index` (Task 4), `db_connector.list_tables_light`/`test_connection` (Task 3 / existing), `sessions.create` (Task 6), `auth.current_user`/`auth.mask_conn_string`.
- Produces: `POST /api/datasources`, `GET /api/datasources`, `POST /api/datasources/{id}/activate`, `PATCH /api/datasources/{id}`, `DELETE /api/datasources/{id}`.

This task's endpoints are FastAPI routes over a live database connection and an LLM call — consistent with this repo's existing convention (`routes_app.py`/`main.py` have zero pytest coverage today; verification is the bash smoke script and manual curl). No new pytest file here; automated coverage is added in Task 9, and this task's own steps include a manual curl check.

- [ ] **Step 1: Rename `_mask_conn_string` to `mask_conn_string` (now used cross-module)**

In `backend/auth.py`, rename the function and its one call site:

```python
def mask_conn_string(cs: Optional[str]) -> Optional[str]:
    """host/db only, credentials and full path stripped — safe to send to the browser."""
    if not cs:
        return None
    masked = re.sub(r"//.*?@", "//***:***@", cs)
    masked = re.sub(r"/[^/]+$", "/***", masked)
    return masked
```

And in `public_user()`, change `"db_host_masked": _mask_conn_string(user.get("db_connection_string")),` to `"db_host_masked": mask_conn_string(user.get("db_connection_string")),`.

- [ ] **Step 2: Add request schemas**

In `backend/schemas.py`, add near `SessionRequest`:

```python
class DataSourceCreate(BaseModel):
    model_config = {"extra": "ignore"}
    type: Literal["postgresql", "inflectiv"]
    label: Optional[str] = None
    conn_string: Optional[str] = None
    global_key: Optional[str] = None
    dataset_id: Optional[int] = None
    dataset_name: Optional[str] = None


class DataSourceRename(BaseModel):
    label: str
```

- [ ] **Step 3: Implement `backend/routes_datasources.py`**

```python
"""Saved data-source management: add, list, activate, rename, delete.
Mounted under /api/datasources by main.py. All routes require auth."""
from __future__ import annotations

import psycopg2.extensions
from fastapi import APIRouter, Depends, HTTPException

import datasources_store as store
import db
import db_connector
import sessions
from auth import current_user, mask_conn_string
from inflectiv import InflectivClient, InflectivError
from schemas import DataSourceCreate, DataSourceRename
from table_indexer import build_table_index

router = APIRouter(prefix="/api/datasources")


def _label_from_conn_string(conn_string: str) -> str:
    try:
        parsed = psycopg2.extensions.parse_dsn(conn_string.replace("postgres://", "postgresql://", 1))
        return parsed.get("dbname") or parsed.get("host") or "PostgreSQL"
    except Exception:
        return "PostgreSQL"


@router.post("")
async def add_data_source(req: DataSourceCreate, user: dict = Depends(current_user)):
    if req.type == "postgresql":
        conn_string = (req.conn_string or "").strip()
        if not conn_string:
            raise HTTPException(400, "Connection string is required.")
        test_result = db_connector.test_connection(conn_string)
        if not test_result.get("ok"):
            raise HTTPException(400, f"Database connection failed: {test_result.get('error')}")
        light = db_connector.list_tables_light(conn_string)
        if not light:
            raise HTTPException(400, "No tables found in the public schema.")
        table_index = await build_table_index(light)
        label = req.label or _label_from_conn_string(conn_string)
        meta = {"host_masked": mask_conn_string(conn_string), "table_count": len(table_index)}
        row = store.create(user["id"], "postgresql", label,
                            {"conn_string": conn_string}, meta, table_index)
        sess = sessions.create(source_type="database", conn_string=conn_string,
                                table_index=table_index, dataset_name=label, data_source_id=row["id"])
    elif req.type == "inflectiv":
        key = (req.global_key or "").strip()
        if not key:
            raise HTTPException(400, "An API key is required.")
        try:
            client = InflectivClient(key)
            dataset = (await client.get_dataset_by_id(req.dataset_id) if req.dataset_id
                       else await client.resolve_name_to_id(req.dataset_name))
        except InflectivError as e:
            raise HTTPException(400, str(e))
        label = req.label or dataset.get("name") or "Inflectiv dataset"
        meta = {"dataset_name": dataset.get("name"),
                "knowledge_source_count": dataset.get("knowledge_source_count", 0)}
        row = store.create(user["id"], "inflectiv", label,
                            {"key": key, "dataset_id": dataset["id"], "dataset_name": dataset.get("name")},
                            meta)
        sess = sessions.create(global_key=key, dataset=dataset, data_source_id=row["id"])
    else:
        raise HTTPException(400, "type must be 'postgresql' or 'inflectiv'.")

    db.log_activity(user["id"], "connect_source", label)
    return {"id": row["id"], "label": row["label"], "type": row["type"], "meta": row["meta"],
            "session_id": sess.session_id}


@router.get("")
def list_data_sources(user: dict = Depends(current_user)):
    return {"sources": store.list_for_user(user["id"])}


@router.post("/{id}/activate")
async def activate_data_source(id: int, user: dict = Depends(current_user)):
    try:
        row = store.activate(id, user["id"])
    except LookupError:
        raise HTTPException(404, "Data source not found.")
    secret = store.decrypt_secret(row)
    if row["type"] == "postgresql":
        sess = sessions.create(source_type="database", conn_string=secret["conn_string"],
                                table_index=row.get("table_index") or [], dataset_name=row["label"],
                                data_source_id=row["id"])
    else:
        dataset = {"id": secret["dataset_id"], "name": secret.get("dataset_name"),
                   "knowledge_source_count": (row.get("meta") or {}).get("knowledge_source_count", 0)}
        sess = sessions.create(global_key=secret["key"], dataset=dataset, data_source_id=row["id"])
    db.log_activity(user["id"], "activate_source", row["label"])
    return {"session_id": sess.session_id}


@router.patch("/{id}")
def rename_data_source(id: int, req: DataSourceRename, user: dict = Depends(current_user)):
    try:
        row = store.rename(id, user["id"], req.label)
    except LookupError:
        raise HTTPException(404, "Data source not found.")
    return {"id": row["id"], "label": row["label"]}


@router.delete("/{id}")
def delete_data_source(id: int, user: dict = Depends(current_user)):
    store.delete(id, user["id"])
    return {"ok": True}
```

- [ ] **Step 4: Wire into `main.py`; drop `/api/db/tables`; make `/api/session`'s database branch auto-select tables**

Add imports near the top of `backend/main.py` (alongside the existing `import routes_app`):

```python
import routes_datasources
from table_indexer import build_table_index
```

After `app.include_router(routes_app.router)`, add:

```python
app.include_router(routes_datasources.router)
```

In `_startup()`, add a soft warning (mirroring the existing DB-unavailable warning style) before the `try: db.init_db()` block:

```python
    if not config.DATA_SOURCE_ENCRYPTION_KEY:
        print("[startup] WARNING: DATA_SOURCE_ENCRYPTION_KEY is not set — saving data "
              "sources will fail until it's configured. Generate one with: python -c "
              "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
```

Replace the `if source_type == "database":` block inside `create_session()` (currently lines 116–155) with:

```python
    if source_type == "database":
        conn_string = (req.conn_string or "").strip()
        if not conn_string and user and user.get("db_connection_string"):
            conn_string = user["db_connection_string"]
        if not conn_string:
            raise HTTPException(400, "Database connection string is required.")

        test_result = db_connector.test_connection(conn_string)
        if not test_result.get("ok"):
            raise HTTPException(400, f"Database connection failed: {test_result.get('error')}")

        light = db_connector.list_tables_light(conn_string)
        if not light:
            raise HTTPException(400, "No tables found in the public schema.")
        table_index = await build_table_index(light)

        key = (req.global_key or "").strip() or config.INFLECTIV_FALLBACK_KEY
        sess = sessions.create(global_key=key, source_type="database",
                               conn_string=conn_string, table_index=table_index,
                               dataset_name=f"{len(table_index)} tables")
        ds = datasource.DatabaseDataSource(conn_string, table_index)
        profile = None
        if config.have_llm():
            try:
                profile = await ds.get_profile(emit=None)
                sess.profile = profile
            except Exception:
                pass
        return {
            "session_id": sess.session_id,
            "source_type": "database",
            "dataset_name": sess.dataset_name,
            "profile": profile.model_dump() if profile else None,
            "suggested": [c.model_dump() for c in (profile.suggested_charts if profile else [])],
            "suggested_queries": (profile.suggested_queries if profile else []),
        }
```

Delete the `@app.post("/api/db/tables")` endpoint entirely (the `db_tables` function). Leave `@app.post("/api/db/test")` (`db_test`) unchanged — still used to validate a connection string before the full add+index flow.

- [ ] **Step 5: Manual verification**

Start the backend (`cd backend && uvicorn main:app --reload --port 8000`) with `DATA_SOURCE_ENCRYPTION_KEY` set in `.env`, then, against a running Postgres with at least one table (e.g. the `dollar-postgres-dev` container referenced by `scripts/test_db_smoke.sh`):

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/signup -H 'Content-Type: application/json' \
  -d '{"email":"plan-check@example.com","password":"test1234"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

curl -s -X POST http://localhost:8000/api/datasources -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"type":"postgresql","conn_string":"postgresql://postgres:postgres@localhost:5432/onedollarstore"}' | python3 -m json.tool
```

Expected: JSON with `id`, `label`, `type: "postgresql"`, `meta.table_count > 0`, `session_id` — and no `conn_string` or `secret_enc` anywhere in the response.

```bash
curl -s http://localhost:8000/api/datasources -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Expected: `{"sources": [{"id": ..., "type": "postgresql", "label": ..., "meta": {...}, "is_active": true, ...}]}`.

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/api/db/tables \
  -H 'Content-Type: application/json' -d '{"conn_string":"x"}'
```

Expected: `404` (endpoint removed).

- [ ] **Step 6: Commit**

```bash
git add backend/auth.py backend/schemas.py backend/routes_datasources.py backend/main.py
git commit -m "Add /api/datasources router; auto-select tables in /api/session; remove /api/db/tables"
```

---

### Task 8: Migrate legacy single-connection users on login

**Files:**
- Modify: `backend/routes_app.py`
- Test: `backend/tests/test_routes_app_migration.py`

**Interfaces:**
- Consumes: `datasources_store` (Task 2), `db_connector.list_tables_light` (Task 3), `table_indexer.build_table_index` (Task 4).
- Produces: `routes_app._migrate_legacy_source(user: dict) -> None` — called from `/auth/login`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_routes_app_migration.py`:

```python
import routes_app


def test_migrate_legacy_source_prefers_postgres_when_both_present(monkeypatch):
    created = []
    monkeypatch.setattr(routes_app.store, "list_for_user", lambda uid: [])
    monkeypatch.setattr(routes_app.store, "create", lambda *a, **kw: created.append(a) or {"id": 1})
    monkeypatch.setattr(routes_app.db_connector, "list_tables_light",
                         lambda cs: [{"table_name": "t", "row_estimate": 1, "columns": [], "foreign_keys": []}])

    async def fake_build_table_index(light):
        return light

    monkeypatch.setattr(routes_app, "build_table_index", fake_build_table_index)

    user = {"id": 1, "db_connection_string": "postgresql://x", "db_table_name": "t",
            "inflectiv_key": "k", "inflectiv_dataset_id": 5, "inflectiv_dataset_name": "ds"}
    routes_app._migrate_legacy_source(user)

    assert len(created) == 1
    assert created[0][1] == "postgresql"


def test_migrate_legacy_source_skips_if_already_migrated(monkeypatch):
    monkeypatch.setattr(routes_app.store, "list_for_user", lambda uid: [{"id": 9}])
    calls = []
    monkeypatch.setattr(routes_app.store, "create", lambda *a, **kw: calls.append(a))
    routes_app._migrate_legacy_source({"id": 1, "db_connection_string": "postgresql://x"})
    assert calls == []


def test_migrate_legacy_source_falls_back_to_inflectiv(monkeypatch):
    created = []
    monkeypatch.setattr(routes_app.store, "list_for_user", lambda uid: [])
    monkeypatch.setattr(routes_app.store, "create", lambda *a, **kw: created.append(a) or {"id": 2})
    user = {"id": 1, "db_connection_string": None, "inflectiv_key": "k",
            "inflectiv_dataset_id": 5, "inflectiv_dataset_name": "ds"}
    routes_app._migrate_legacy_source(user)
    assert len(created) == 1
    assert created[0][1] == "inflectiv"


def test_migrate_legacy_source_noop_for_fresh_user(monkeypatch):
    monkeypatch.setattr(routes_app.store, "list_for_user", lambda uid: [])
    calls = []
    monkeypatch.setattr(routes_app.store, "create", lambda *a, **kw: calls.append(a))
    routes_app._migrate_legacy_source({"id": 1, "db_connection_string": None, "inflectiv_key": None})
    assert calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_routes_app_migration.py -v`
Expected: FAIL — `AttributeError: module 'routes_app' has no attribute '_migrate_legacy_source'`

- [ ] **Step 3: Implement in `backend/routes_app.py`**

Add imports at the top:

```python
import asyncio

import db_connector
import datasources_store as store
from table_indexer import build_table_index
```

Replace the `login` function:

```python
@router.post("/auth/login")
def login(req: LoginRequest):
    user = db.one("SELECT * FROM users WHERE email=%s", (req.email.strip().lower(),))
    if not user or not auth.verify_pw(user, req.password):
        raise HTTPException(401, "Invalid email or password.")
    db.log_activity(user["id"], "login", user["email"])
    _migrate_legacy_source(user)
    return {"token": user["api_token"], "user": auth.public_user(user)}


def _migrate_legacy_source(user: dict) -> None:
    """One-time: if this user has legacy flat-column DB/Inflectiv config and no
    saved data_sources row yet, promote it into the new table so they don't have
    to re-enter credentials. Runs once, guarded by the existing-rows check."""
    if store.list_for_user(user["id"]):
        return
    if user.get("db_connection_string"):
        try:
            _migrate_db_source(user)
        except Exception as e:
            print(f"[migrate] db source skipped for user {user['id']}: {e}")
    elif user.get("inflectiv_key"):
        store.create(user["id"], "inflectiv",
                     user.get("inflectiv_dataset_name") or "Inflectiv dataset",
                     {"key": user["inflectiv_key"], "dataset_id": user.get("inflectiv_dataset_id"),
                      "dataset_name": user.get("inflectiv_dataset_name")},
                     {"dataset_name": user.get("inflectiv_dataset_name"), "knowledge_source_count": 0})


def _migrate_db_source(user: dict) -> None:
    conn_string = user["db_connection_string"]
    light = db_connector.list_tables_light(conn_string)
    table_index = asyncio.run(build_table_index(light)) if light else []
    store.create(user["id"], "postgresql", user.get("db_table_name") or "PostgreSQL",
                 {"conn_string": conn_string},
                 {"host_masked": auth.mask_conn_string(conn_string), "table_count": len(table_index)},
                 table_index)
```

- [ ] **Step 4: Trim `PATCH /api/me` and `ProfileUpdate`**

In `backend/schemas.py`, replace `ProfileUpdate`:

```python
class ProfileUpdate(BaseModel):
    model_config = {"extra": "ignore"}
    name: Optional[str] = None
    company: Optional[str] = None
    ai_prefs: Optional[dict] = None
```

In `backend/routes_app.py`, replace the field loop in `update_me`:

```python
@router.patch("/me")
def update_me(req: ProfileUpdate, user: dict = Depends(current_user)):
    fields, vals = [], []
    for k in ("name", "company"):
        v = getattr(req, k)
        if v is not None:
            fields.append(f"{k}=%s"); vals.append(v)
    if req.ai_prefs is not None:
        fields.append("ai_prefs=%s"); vals.append(db.Json(req.ai_prefs))
    if fields:
        vals.append(user["id"])
        db.execute(f"UPDATE users SET {','.join(fields)} WHERE id=%s", tuple(vals))
    return {"user": auth.public_user(db.one("SELECT * FROM users WHERE id=%s", (user["id"],)))}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_routes_app_migration.py -v`
Expected: `4 passed`

- [ ] **Step 6: Commit**

```bash
git add backend/routes_app.py backend/schemas.py backend/tests/test_routes_app_migration.py
git commit -m "Auto-migrate legacy single-connection users to data_sources on login"
```

---

### Task 9: Extend the smoke test for the new endpoints

**Files:**
- Modify: `scripts/test_db_smoke.sh`

**Interfaces:**
- Consumes: all of Tasks 1–8's shipped behavior end-to-end against a real Postgres.

The existing script's section 10 ("Edge: session without table_name") currently asserts that an *empty* `table_name` makes `/api/session` **fail** — that assumption is now wrong (Task 7 makes `table_name` unnecessary entirely). This step corrects that section and adds coverage for the new endpoints.

- [ ] **Step 1: Fix section 10 — empty/absent table_name now succeeds**

Replace the existing section (currently reads, roughly):

```bash
# ── 10. Edge: no key mode ────────────────────────────────────────────────────
echo ""
echo "── 10. Edge: session without table_name ──"
RES=$(curl -sf --max-time 5 -X POST "$BASE/session" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"source_type": "database", "conn_string": "'"$DB_URL"'", "table_name": ""}' 2>&1) \
  && nok "Empty table_name should fail" \
  || ok "Empty table_name rejected correctly"
```

with:

```bash
# ── 10. /api/session without table_name now auto-selects tables ─────────────
echo ""
echo "── 10. POST /api/session without table_name (auto table selection) ──"
RES=$(curl -sf --max-time 60 -X POST "$BASE/session" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"source_type": "database", "conn_string": "'"$DB_URL"'"}') \
  || { nok "Session without table_name failed"; exit 1; }
echo "$RES" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['source_type'] == 'database'
assert d['profile'] is not None
" && ok "Session created without a table_name (auto table selection)" || nok "Auto table selection session malformed"
```

- [ ] **Step 2: Add new-endpoint coverage before the `# ── Summary ──` block**

Insert before the `# ── Summary ──` section at the end of the file:

```bash
# ── 16. POST /api/datasources (new saved source, auto table selection) ──────
echo ""
echo "── 16. POST /api/datasources ──"
RES=$(curl -sf --max-time 60 -X POST "$BASE/datasources" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{\"type\": \"postgresql\", \"conn_string\": \"$DB_URL\"}") || { nok "Add data source failed"; exit 1; }
DS_ID=$(echo "$RES" | extract "['id']")
echo "  → data source id: $DS_ID"
echo "$RES" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['type'] == 'postgresql'
assert d['session_id']
assert 'conn_string' not in json.dumps(d), 'raw connection string leaked in response'
" && ok "Data source added, no credentials in response" || nok "Add data source response malformed"

# ── 17. GET /api/datasources — list is masked, exactly one active ───────────
echo ""
echo "── 17. GET /api/datasources ──"
curl -sf --max-time 5 "$BASE/datasources" -H "Authorization: Bearer $TOKEN" | python3 -c "
import sys, json
d = json.load(sys.stdin)
sources = d['sources']
assert len(sources) >= 1
assert all('secret_enc' not in s and 'conn_string' not in json.dumps(s) for s in sources), 'secret leaked'
active = [s for s in sources if s['is_active']]
assert len(active) == 1, f'expected exactly one active source, got {len(active)}'
" && ok "Datasources listed, masked, exactly one active" || nok "Datasources list malformed"

# ── 18. Activate + generate against the auto-selected table(s) ──────────────
echo ""
echo "── 18. POST /api/datasources/\$DS_ID/activate, then /api/generate ──"
RES=$(curl -sf --max-time 30 -X POST "$BASE/datasources/$DS_ID/activate" \
  -H "Authorization: Bearer $TOKEN") || { nok "Activate failed"; exit 1; }
SID2=$(echo "$RES" | extract "['session_id']")
[ -n "$SID2" ] && ok "Reactivated without re-sending credentials" || nok "Activate returned no session_id"

RES=$(curl -sf --max-time 120 -X POST "$BASE/generate" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{\"session_id\": \"$SID2\", \"goal\": \"Show me top selling products by quantity\"}") \
  || { nok "Generate (auto table) failed"; exit 1; }
echo "$RES" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['status'] == 'ready'
assert len(d.get('drafts', [])) > 0
" && ok "Generated using auto-selected table(s)" || nok "Generate (auto table) produced no drafts"

# ── 19. POST /api/db/tables is gone ──────────────────────────────────────────
echo ""
echo "── 19. POST /api/db/tables removed ──"
CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -X POST "$BASE/db/tables" \
  -H "Content-Type: application/json" -d "{\"conn_string\": \"$DB_URL\"}")
[ "$CODE" = "404" ] && ok "/api/db/tables removed (404)" || nok "/api/db/tables still responds ($CODE)"
```

- [ ] **Step 3: Run the smoke test**

Run: `bash scripts/test_db_smoke.sh` (requires docker running, `dollar-postgres-dev` container up, backend running on `localhost:8000` with `DATA_SOURCE_ENCRYPTION_KEY` and `ALLOW_PRIVATE_DB_HOSTS=true` set)
Expected: all sections print `✓`, final tally shows `0 failed`.

- [ ] **Step 4: Commit**

```bash
git add scripts/test_db_smoke.sh
git commit -m "Extend DB smoke test for auto table selection and saved data sources"
```

---

### Task 10: Frontend — signup wizard connect step collapses to one field

**Files:**
- Modify: `frontend/Agentic Auth.dc.html`

**Interfaces:**
- Consumes: `POST /api/db/test` (existing, unchanged), `POST /auth/signup` (existing, `db_table_name` now always sent as `null`).

- [ ] **Step 1: Trim the `su` state object**

`frontend/Agentic Auth.dc.html` currently has (around line 412):

```javascript
      connString: '', tableName: '', tables: [], tablesLoading: false, dbTested: false,
```

Replace with:

```javascript
      connString: '', dbTested: false, dbTesting: false,
```

- [ ] **Step 2: Replace `loadSuTables()` with `testSuConnection()`**

The method currently reads (around lines 465–479):

```javascript
  async loadSuTables() {
    const cs = this.state.su.connString.trim();
    if (!cs) { this.setSu('dsError', 'Enter a connection string first.'); return; }
    this.setSu('tablesLoading', true); this.setSu('dsError', '');
    try {
      const res = await fetch(BACKEND + '/db/tables', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ conn_string: cs }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
      const list = (data.tables || []).filter(t => t.table_name);
      if (!list.length) throw new Error('No tables found.');
      this.setState(s => ({ su: Object.assign({}, s.su, { tables: list, tableName: list[0].table_name, tablesLoading: false, dbTested: true, dsError: '' }) }));
    } catch (e) {
      this.setState(s => ({ su: Object.assign({}, s.su, { tablesLoading: false, dsError: String(e.message || e), dbTested: false }) }));
    }
  }
```

Replace with:

```javascript
  async testSuConnection() {
    const cs = this.state.su.connString.trim();
    if (!cs) { this.setSu('dsError', 'Enter a connection string first.'); return; }
    this.setSu('dbTesting', true); this.setSu('dsError', '');
    try {
      const res = await fetch(BACKEND + '/db/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ conn_string: cs }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
      this.setState(s => ({ su: Object.assign({}, s.su, { dbTesting: false, dbTested: true, dsError: '' }) }));
    } catch (e) {
      this.setState(s => ({ su: Object.assign({}, s.su, { dbTesting: false, dsError: String(e.message || e), dbTested: false }) }));
    }
  }
```

- [ ] **Step 3: Fix the signup payload — `db_table_name` is always `null` now**

Around line 503, currently:

```javascript
      db_table_name: su.sourceType === 'database' ? su.tableName.trim() : null,
```

Replace with:

```javascript
      db_table_name: null,
```

- [ ] **Step 4: Simplify the render bindings**

Currently (around lines 703–709):

```javascript
      su_connString: su.connString, onSuConnString: (e) => this.setSu('connString', e.target.value),
      su_tableName: su.tableName, onSuTableName: (e) => this.setSu('tableName', e.target.value),
      onSuTableSelect: (e) => this.setSu('tableName', e.target.value),
      suDbTested: su.dbTested,
      loadSuTables: () => this.loadSuTables(), tablesLoadLabel: su.tablesLoading ? 'Loading…' : 'Load tables',
      hasSuTables: su.tables.length > 0,
      suTableOptions: su.tables.map(t => ({ value: t.table_name, label: t.table_name + (t.row_estimate ? ' · ~' + t.row_estimate + ' rows' : '') })),
```

Replace with:

```javascript
      su_connString: su.connString, onSuConnString: (e) => this.setSu('connString', e.target.value),
      suDbTested: su.dbTested,
      testSuConnection: () => this.testSuConnection(), dbTestLabel: su.dbTesting ? 'Testing…' : 'Test connection',
```

- [ ] **Step 5: Replace the connect-step markup**

The data-source connection block currently reads (search for `Connect PostgreSQL database`):

```html
<sc-if value="{{ isSuSourceDatabase }}" hint-placeholder-val="{{ false }}">
  <div style="padding:16px;border:1px solid var(--violet-border);border-radius:14px;background:var(--violet-bg);">
    <div style="font-size:13px;font-weight:700;color:var(--ink-900);margin-bottom:3px;">Connect PostgreSQL database</div>
    <div style="font-size:11.5px;color:var(--ink-500);line-height:1.5;margin-bottom:12px;">Enter your PostgreSQL connection string. The agent will introspect the schema and query tables directly.</div>
    <div style="display:flex;gap:8px;margin-bottom:10px;">
      <input value="{{ su_connString }}" onInput="{{ onSuConnString }}" type="password" placeholder="postgresql://user:pass@host:5432/dbname" style="flex:1;height:42px;padding:0 13px;border:1px solid var(--border);border-radius:10px;background:var(--bg-panel);font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--ink-900);outline:none;" />
      <sc-if value="{{ suDbTested }}" hint-placeholder-val="{{ false }}"><span style="display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:600;color:var(--emerald-ink);background:var(--emerald-bg);padding:4px 10px;border-radius:8px;"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>Connected</span></sc-if>
    </div>
    <div style="display:flex;gap:8px;">
      <button onMouseDown="{{ loadSuTables }}" style="height:42px;padding:0 16px;border:none;border-radius:10px;background:var(--violet);color:#fff;font-family:'Hanken Grotesk',sans-serif;font-weight:600;font-size:13px;cursor:pointer;flex:0 0 auto;">{{ tablesLoadLabel }}</button>
      <input value="{{ su_tableName }}" onInput="{{ onSuTableName }}" placeholder="table_name" style="flex:1;height:42px;padding:0 13px;border:1px solid var(--border);border-radius:10px;background:var(--bg-panel);font-family:'IBM Plex Mono',monospace;font-size:12.5px;color:var(--ink-900);outline:none;" />
    </div>
    <sc-if value="{{ hasSuTables }}" hint-placeholder-val="{{ false }}">
      <select value="{{ su_tableName }}" onChange="{{ onSuTableSelect }}" style="width:100%;height:42px;margin-top:10px;padding:0 13px;border:1px solid var(--border);border-radius:10px;background:var(--bg-panel);font-family:'Hanken Grotesk',sans-serif;font-size:14px;color:var(--ink-900);outline:none;cursor:pointer;">
        <sc-for list="{{ suTableOptions }}" as="opt" hint-placeholder-count="3"><option value="{{ opt.value }}">{{ opt.label }}</option></sc-for>
      </select>
    </sc-if>
    <sc-if value="{{ dsError }}" hint-placeholder-val="{{ false }}"><div style="margin-top:10px;font-size:12px;color:var(--rose);">{{ dsError }}</div></sc-if>
  </div>
</sc-if>
```

Replace with:

```html
<sc-if value="{{ isSuSourceDatabase }}" hint-placeholder-val="{{ false }}">
  <div style="padding:16px;border:1px solid var(--violet-border);border-radius:14px;background:var(--violet-bg);">
    <div style="font-size:13px;font-weight:700;color:var(--ink-900);margin-bottom:3px;">Connect PostgreSQL database</div>
    <div style="font-size:11.5px;color:var(--ink-500);line-height:1.5;margin-bottom:12px;">Paste your connection string. The agent automatically finds and queries the right table(s) for every question — no need to know your schema.</div>
    <div style="display:flex;gap:8px;">
      <input value="{{ su_connString }}" onInput="{{ onSuConnString }}" type="password" placeholder="postgresql://user:pass@host:5432/dbname" style="flex:1;height:42px;padding:0 13px;border:1px solid var(--border);border-radius:10px;background:var(--bg-panel);font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--ink-900);outline:none;" />
      <button onMouseDown="{{ testSuConnection }}" style="height:42px;padding:0 16px;border:none;border-radius:10px;background:var(--violet);color:#fff;font-family:'Hanken Grotesk',sans-serif;font-weight:600;font-size:13px;cursor:pointer;flex:0 0 auto;">{{ dbTestLabel }}</button>
    </div>
    <sc-if value="{{ suDbTested }}" hint-placeholder-val="{{ false }}"><div style="margin-top:10px;"><span style="display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:600;color:var(--emerald-ink);background:var(--emerald-bg);padding:4px 10px;border-radius:8px;"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>Connected</span></div></sc-if>
    <sc-if value="{{ dsError }}" hint-placeholder-val="{{ false }}"><div style="margin-top:10px;font-size:12px;color:var(--rose);">{{ dsError }}</div></sc-if>
  </div>
</sc-if>
```

- [ ] **Step 6: Verify with the browser**

1. `mcp__plugin_playwright_playwright__browser_navigate` to `http://localhost:8000/Agentic%20Auth.dc.html`
2. `mcp__plugin_playwright_playwright__browser_snapshot` — confirm the signup flow's data-source step shows only a connection-string field + "Test connection" button (no table dropdown, no "table_name" text input).
3. Fill the connection string field with `postgresql://postgres:postgres@localhost:5432/onedollarstore` (or another reachable test DB), click "Test connection".
4. `mcp__plugin_playwright_playwright__browser_snapshot` — confirm the "Connected" badge appears and no error is shown.
5. Complete signup (fill name/email/password, accept terms) and confirm it succeeds (lands on the dashboard) — this exercises `db_table_name: null` reaching `/auth/signup` without error.

- [ ] **Step 7: Commit**

```bash
git add "frontend/Agentic Auth.dc.html"
git commit -m "Simplify signup wizard's PostgreSQL step to a single connection-string field"
```

---

### Task 11: Frontend — Datasets tab lists, activates, renames, deletes saved sources

**Files:**
- Modify: `frontend/Agentic App.dc.html`

**Interfaces:**
- Consumes: `GET /api/datasources`, `POST /api/datasources`, `POST /api/datasources/{id}/activate`, `PATCH /api/datasources/{id}`, `DELETE /api/datasources/{id}` (Task 7).

- [ ] **Step 1: Fetch saved sources in `loadAll()`**

`frontend/Agentic App.dc.html`'s `loadAll()` currently reads:

```javascript
    const [act, comps, ins, ds, dash, setres, team] = await Promise.all([
      get('/activity'), get('/components'), get('/insights'), get('/my-datasets'), get('/dashboards'), get('/settings'), get('/team')
    ]);
    const patch = { activityData: act && act.activity, compData: comps && comps.components, insightData: ins && ins.insights, dsData: ds && ds.datasets, dashData: dash && dash.dashboards, teamData: team && team.members };
```

Replace with:

```javascript
    const [act, comps, ins, ds, dash, setres, team, srcs] = await Promise.all([
      get('/activity'), get('/components'), get('/insights'), get('/my-datasets'), get('/dashboards'), get('/settings'), get('/team'), get('/datasources')
    ]);
    const patch = { activityData: act && act.activity, compData: comps && comps.components, insightData: ins && ins.insights, dsData: ds && ds.datasets, dashData: dash && dash.dashboards, teamData: team && team.members, dsSources: (srcs && srcs.sources) || [] };
```

- [ ] **Step 2: Add action methods**

Add these methods alongside `loadAll()` (same object, e.g. right after it):

```javascript
  async activateSource(id) {
    try {
      const res = await fetch(BACKEND + '/datasources/' + id + '/activate', { method: 'POST', headers: authHeaders() });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Could not activate this source.');
      await this.loadAll();
    } catch (e) { this.setState({ dsActionError: String(e.message || e) }); }
  },
  async deleteSource(id) {
    try {
      const res = await fetch(BACKEND + '/datasources/' + id, { method: 'DELETE', headers: authHeaders() });
      if (!res.ok) { const data = await res.json(); throw new Error(data.detail || 'Could not remove this source.'); }
      await this.loadAll();
    } catch (e) { this.setState({ dsActionError: String(e.message || e) }); }
  },
  startRenameSource(id, currentLabel) {
    this.setState({ renamingSourceId: id, renamingSourceLabel: currentLabel });
  },
  async commitRenameSource() {
    const id = this.state.renamingSourceId;
    const label = (this.state.renamingSourceLabel || '').trim();
    if (!id || !label) { this.setState({ renamingSourceId: null }); return; }
    try {
      const res = await fetch(BACKEND + '/datasources/' + id, { method: 'PATCH', headers: authHeaders(), body: JSON.stringify({ label }) });
      if (!res.ok) { const data = await res.json(); throw new Error(data.detail || 'Could not rename this source.'); }
      this.setState({ renamingSourceId: null });
      await this.loadAll();
    } catch (e) { this.setState({ dsActionError: String(e.message || e), renamingSourceId: null }); }
  },
  async addSource() {
    const a = this.state.addSrc;
    this.setState(s => ({ addSrc: Object.assign({}, s.addSrc, { busy: true, error: '' }) }));
    const body = a.type === 'postgresql'
      ? { type: 'postgresql', conn_string: a.connString.trim() }
      : { type: 'inflectiv', global_key: a.globalKey.trim(), dataset_id: a.datasetId ? parseInt(a.datasetId, 10) : null, dataset_name: a.datasetName || null };
    try {
      const res = await fetch(BACKEND + '/datasources', { method: 'POST', headers: authHeaders(), body: JSON.stringify(body) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Could not add this data source.');
      this.setState({ addSrc: { type: 'postgresql', connString: '', globalKey: '', datasetId: '', datasetName: '', busy: false, error: '' }, showAddSource: false });
      await this.loadAll();
    } catch (e) {
      this.setState(s => ({ addSrc: Object.assign({}, s.addSrc, { busy: false, error: String(e.message || e) }) }));
    }
  },
```

Add the initial state for these fields to the component's `state` object (wherever `dsData`/`activityData` etc. are initialized — add alongside them):

```javascript
    dsSources: [], dsActionError: '', renamingSourceId: null, renamingSourceLabel: '',
    showAddSource: false,
    addSrc: { type: 'postgresql', connString: '', globalKey: '', datasetId: '', datasetName: '', busy: false, error: '' },
```

- [ ] **Step 3: Source the datasets list from `dsSources` instead of `dsData`/`s.me.db_table_name`**

The datasets-rendering block currently reads:

```javascript
    const rawDatasets = [
      ...((s.dsData && s.dsData.length)
        ? s.dsData.map(d => ({
            name: d.name || d.api_name || 'Untitled', path: d.api_name || '', source: 'Inflectiv',
            rows: String(d.knowledge_source_count != null ? d.knowledge_source_count : '—'), cols: String(d.vector_count || '—'),
            updated: timeAgo(d.updated_at || d.created_at), status: 'Synced',
            letter: (d.name || 'D')[0].toUpperCase(), color: 'var(--violet)', tint: 'var(--violet-bg)', owner: ownerName, oc: '#6E56CF'
          }))
        : []),
      ...((s.me && s.me.has_db && s.me.db_table_name)
        ? [{
            name: s.me.db_table_name, path: s.me.db_type || 'postgresql', source: 'PostgreSQL',
            rows: '—', cols: '—',
            updated: 'just now', status: 'Ready',
            letter: (s.me.db_table_name || 'D')[0].toUpperCase(), color: 'var(--emerald)', tint: 'var(--emerald-bg)', owner: ownerName, oc: '#0E9F6E'
          }]
        : [])
    ];
    const datasets = rawDatasets.map(d => Object.assign({}, d, {
      statusStyle: statusStyle(ST[d.status] || ST.Synced), statusDot: (ST[d.status] || ST.Synced).dot,
      ownerColor: d.oc, ownerInitials: (d.owner || 'Y').split(' ').map(w => w[0]).join('').slice(0, 2)
    }));
```

Replace with:

```javascript
    const rawDatasets = (s.dsSources || []).map(d => {
      const isPg = d.type === 'postgresql';
      const meta = d.meta || {};
      return {
        id: d.id,
        name: d.label, path: isPg ? (meta.host_masked || '') : (meta.dataset_name || ''),
        source: isPg ? 'PostgreSQL' : 'Inflectiv',
        rows: isPg ? String(meta.table_count != null ? meta.table_count + ' tables' : '—') : String(meta.knowledge_source_count != null ? meta.knowledge_source_count : '—'),
        cols: '—',
        updated: d.last_connected_at ? timeAgo(d.last_connected_at) : 'never',
        status: d.is_active ? 'Active' : 'Saved',
        letter: (d.label || 'D')[0].toUpperCase(),
        color: isPg ? 'var(--emerald)' : 'var(--violet)', tint: isPg ? 'var(--emerald-bg)' : 'var(--violet-bg)',
        owner: ownerName, oc: isPg ? '#0E9F6E' : '#6E56CF',
        isActive: d.is_active,
        onActivate: () => this.activateSource(d.id),
        onRename: () => this.startRenameSource(d.id, d.label),
        onDelete: () => this.deleteSource(d.id)
      };
    });
    const datasets = rawDatasets.map(d => Object.assign({}, d, {
      statusStyle: statusStyle(d.isActive ? ST.Ready : ST.Synced), statusDot: (d.isActive ? ST.Ready : ST.Synced).dot,
      ownerColor: d.oc, ownerInitials: (d.owner || 'Y').split(' ').map(w => w[0]).join('').slice(0, 2)
    }));
```

- [ ] **Step 4: Add per-row actions and an "Add data source" panel**

The row template (search for `sc-for list="{{ datasets }}"` in `frontend/Agentic App.dc.html`) currently reads:

```html
<sc-for list="{{ datasets }}" as="d" hint-placeholder-count="7">
  <div style-hover="{{ rowHover }}" style="display:grid;grid-template-columns:2.4fr 1.1fr 0.8fr 0.7fr 1.1fr 1fr 0.9fr;gap:12px;padding:13px 18px;border-bottom:1px solid var(--border);align-items:center;cursor:pointer;transition:background .12s;">
    <div style="display:flex;align-items:center;gap:10px;min-width:0;">
      <span style="width:30px;height:30px;border-radius:8px;background:{{ d.tint }};color:{{ d.color }};display:flex;align-items:center;justify-content:center;font-family:'Newsreader',serif;font-weight:600;font-size:13px;flex:0 0 auto;">{{ d.letter }}</span>
      <div style="min-width:0;">
        <div style="font-size:13px;font-weight:600;color:var(--ink-900);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{{ d.name }}</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:var(--ink-400);">{{ d.path }}</div>
      </div>
    </div>
    <span style="font-size:12px;color:var(--ink-700);">{{ d.source }}</span>
    <span style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--ink-900);text-align:right;">{{ d.rows }}</span>
    <span style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--ink-700);text-align:right;">{{ d.cols }}</span>
    <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--ink-500);text-align:right;">{{ d.updated }}</span>
    <span style="display:flex;justify-content:flex-start;"><span style="{{ d.statusStyle }}"><span style="width:5px;height:5px;border-radius:99px;background:{{ d.statusDot }};"></span>{{ d.status }}</span></span>
  </div>
</sc-for>
```

Replace it with (grid gains an 8th `1.6fr` column for actions; a new action-buttons `<div>` is added as the last child):

```html
<sc-for list="{{ datasets }}" as="d" hint-placeholder-count="7">
  <div style-hover="{{ rowHover }}" style="display:grid;grid-template-columns:2.4fr 1.1fr 0.8fr 0.7fr 1.1fr 1fr 0.9fr 1.6fr;gap:12px;padding:13px 18px;border-bottom:1px solid var(--border);align-items:center;cursor:pointer;transition:background .12s;">
    <div style="display:flex;align-items:center;gap:10px;min-width:0;">
      <span style="width:30px;height:30px;border-radius:8px;background:{{ d.tint }};color:{{ d.color }};display:flex;align-items:center;justify-content:center;font-family:'Newsreader',serif;font-weight:600;font-size:13px;flex:0 0 auto;">{{ d.letter }}</span>
      <div style="min-width:0;">
        <div style="font-size:13px;font-weight:600;color:var(--ink-900);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{{ d.name }}</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:var(--ink-400);">{{ d.path }}</div>
      </div>
    </div>
    <span style="font-size:12px;color:var(--ink-700);">{{ d.source }}</span>
    <span style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--ink-900);text-align:right;">{{ d.rows }}</span>
    <span style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--ink-700);text-align:right;">{{ d.cols }}</span>
    <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--ink-500);text-align:right;">{{ d.updated }}</span>
    <span style="display:flex;justify-content:flex-start;"><span style="{{ d.statusStyle }}"><span style="width:5px;height:5px;border-radius:99px;background:{{ d.statusDot }};"></span>{{ d.status }}</span></span>
    <div style="display:flex;gap:6px;justify-content:flex-end;">
      <sc-if value="{{ d.isActive }}" hint-placeholder-val="{{ false }}">
        <span style="font-size:11px;font-weight:600;color:var(--emerald-ink);padding:4px 8px;">Active</span>
      </sc-if>
      <sc-if value="{{ d.isActive }}" hint-placeholder-val="{{ true }}">
        <button onClick="{{ d.onActivate }}" style="height:26px;padding:0 10px;border:1px solid var(--border);border-radius:7px;background:var(--bg-panel);color:var(--ink-700);font-size:11.5px;font-weight:600;cursor:pointer;">Set active</button>
      </sc-if>
      <button onClick="{{ d.onRename }}" style="height:26px;padding:0 10px;border:1px solid var(--border);border-radius:7px;background:var(--bg-panel);color:var(--ink-700);font-size:11.5px;font-weight:600;cursor:pointer;">Rename</button>
      <button onClick="{{ d.onDelete }}" style="height:26px;padding:0 10px;border:1px solid var(--border);border-radius:7px;background:var(--bg-panel);color:var(--rose);font-size:11.5px;font-weight:600;cursor:pointer;">Delete</button>
    </div>
  </div>
</sc-for>
```

(`sc-if` has no built-in "else" — the two `sc-if value="{{ d.isActive }}"` blocks above with opposite `hint-placeholder-val` are this codebase's existing pattern for if/else, matching how `hasSuTables`-style toggles are used elsewhere in these files.)

The header row directly above this `sc-for` (labeling each column: Name, Source, Rows, Cols, Updated, Status) also uses a 7-value `grid-template-columns` to stay aligned with the data rows — find it immediately preceding the `sc-for` block and append a matching 8th `1.6fr` (with an "Actions" label in that column, or leave it blank) so headers and data rows line up.

3. Add an inline rename control: immediately after the closing `</sc-if>` of the "Data source" card section from Task 7's context (or any convenient spot inside the Datasets tab's top-level container), add:

```html
<sc-if value="{{ isDatasets }}" hint-placeholder-val="{{ false }}">
  <sc-if value="{{ renamingSourceId }}" hint-placeholder-val="{{ false }}">
    <div style="position:fixed;inset:0;background:rgba(0,0,0,.3);display:flex;align-items:center;justify-content:center;z-index:50;">
      <div style="background:var(--bg-panel);border-radius:14px;padding:20px;width:360px;box-shadow:var(--shadow-lg);">
        <div style="font-weight:600;margin-bottom:10px;">Rename data source</div>
        <input value="{{ renamingSourceLabel }}" onInput="{{ onRenamingSourceLabel }}" style="width:100%;height:38px;padding:0 12px;border:1px solid var(--border);border-radius:9px;margin-bottom:14px;" />
        <div style="display:flex;gap:8px;justify-content:flex-end;">
          <button onClick="{{ cancelRenameSource }}" style="height:34px;padding:0 12px;border:1px solid var(--border);border-radius:8px;background:var(--bg-panel);cursor:pointer;">Cancel</button>
          <button onClick="{{ commitRenameSource }}" style="height:34px;padding:0 14px;border:none;border-radius:8px;background:var(--violet);color:#fff;font-weight:600;cursor:pointer;">Save</button>
        </div>
      </div>
    </div>
  </sc-if>
</sc-if>
```

Add the matching bindings in `renderVals()` alongside the existing dataset bindings:

```javascript
renamingSourceId: s.renamingSourceId, renamingSourceLabel: s.renamingSourceLabel,
onRenamingSourceLabel: (e) => this.setState({ renamingSourceLabel: e.target.value }),
cancelRenameSource: () => this.setState({ renamingSourceId: null }),
commitRenameSource: () => this.commitRenameSource(),
```

4. Add an "Add data source" trigger button near the "All datasets" table header (find the header text, likely `Dataset Management` or `All datasets`) and its panel — add the button:

```html
<button onClick="{{ toggleAddSource }}" style="height:36px;padding:0 14px;border:none;border-radius:10px;background:var(--violet);color:#fff;font-weight:600;font-size:13px;cursor:pointer;">+ Add data source</button>
```

and, in the same section, the panel (shown when `showAddSource` is true):

```html
<sc-if value="{{ showAddSource }}" hint-placeholder-val="{{ false }}">
  <div style="background:var(--bg-panel);border:1px solid var(--border);border-radius:14px;padding:18px;margin-top:12px;">
    <input value="{{ addSrcConnString }}" onInput="{{ onAddSrcConnString }}" type="password" placeholder="postgresql://user:pass@host:5432/dbname" style="width:100%;height:40px;padding:0 12px;border:1px solid var(--border);border-radius:9px;font-family:'IBM Plex Mono',monospace;font-size:12px;margin-bottom:10px;" />
    <sc-if value="{{ addSrcError }}" hint-placeholder-val="{{ false }}"><div style="color:var(--rose);font-size:12px;margin-bottom:10px;">{{ addSrcError }}</div></sc-if>
    <button onClick="{{ submitAddSource }}" style="height:38px;padding:0 16px;border:none;border-radius:9px;background:var(--violet);color:#fff;font-weight:600;cursor:pointer;">{{ addSrcLabel }}</button>
  </div>
</sc-if>
```

with bindings:

```javascript
showAddSource: s.showAddSource,
toggleAddSource: () => this.setState({ showAddSource: !s.showAddSource }),
addSrcConnString: s.addSrc.connString, onAddSrcConnString: (e) => this.setState(st => ({ addSrc: Object.assign({}, st.addSrc, { connString: e.target.value }) })),
addSrcError: s.addSrc.error,
submitAddSource: () => this.addSource(),
addSrcLabel: s.addSrc.busy ? 'Connecting…' : 'Connect',
```

(This panel covers the Postgres path end-to-end, matching the primary use case in the spec; the Inflectiv `type` toggle in `addSrc` state is left available for a follow-up pass, not required for this task's verification.)

- [ ] **Step 5: Update the Profile page's "Connected database" card to reflect the active saved source**

`frontend/Agentic App.dc.html` currently binds (around line 1200):

```javascript
      hasDbSource: s.me && s.me.has_db && s.me.db_table_name,
      dbTableName: (s.me && s.me.db_table_name) || '',
      dbConnStringMasked: (s.me && s.me.db_host_masked) || ''
```

Replace with (sourcing from the live `dsSources` list rather than the legacy flat columns, so this card stays accurate after a user switches active sources):

```javascript
      hasDbSource: (s.dsSources || []).some(d => d.type === 'postgresql' && d.is_active),
      dbTableName: (((s.dsSources || []).find(d => d.type === 'postgresql' && d.is_active) || {}).meta || {}).table_count + ' tables',
      dbConnStringMasked: (((s.dsSources || []).find(d => d.type === 'postgresql' && d.is_active) || {}).meta || {}).host_masked || ''
```

- [ ] **Step 6: Verify with the browser**

1. `mcp__plugin_playwright_playwright__browser_navigate` to `http://localhost:8000/Agentic%20App.dc.html#datasets` (log in first if needed, using an account created in Task 10's verification or the smoke script).
2. `mcp__plugin_playwright_playwright__browser_snapshot` — confirm the "All datasets" table shows the saved source(s) with an "Active" indicator and a "+ Add data source" button.
3. Click "+ Add data source", type a second (different) reachable connection string, click "Connect". `browser_snapshot` again — confirm a second row appears and is marked active, and the first row now shows "Set active" instead of "Active".
4. Click "Set active" on the first row. `browser_snapshot` — confirm the active indicator moved back, with no credential input required.
5. Click "Rename" on a row, change the label in the modal, click "Save". `browser_snapshot` — confirm the row's name updated.
6. Click "Delete" on the inactive row. `browser_snapshot` — confirm it's gone from the list.

- [ ] **Step 7: Commit**

```bash
git add "frontend/Agentic App.dc.html"
git commit -m "Datasets tab: list, activate, rename, and delete saved data sources"
```

---

## After all tasks

Run the full backend test suite and the smoke test one more time to confirm nothing regressed:

```bash
cd backend && python -m pytest -v
bash ../scripts/test_db_smoke.sh
```

Expected: all pytest tests pass; smoke test tally shows `0 failed`.
