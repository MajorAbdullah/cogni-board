import routes_app


def test_migrate_legacy_source_migrates_both_and_prefers_postgres_when_both_present(monkeypatch):
    """Spec (§6): when a legacy user has BOTH a Postgres connection string and an
    Inflectiv key, both must be migrated into separate data_sources rows, with
    Postgres left active. store.create() always deactivates every other row
    before activating the new one, so the LAST create() call wins as active -
    this asserts both were created, in order (Inflectiv then Postgres), so
    Postgres ends up active."""
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

    # Both legacy sources must be migrated - neither is silently dropped.
    assert len(created) == 2
    assert created[0][1] == "inflectiv"
    assert created[1][1] == "postgresql"
    # Whichever store.create() call runs LAST wins as the active row, so
    # Postgres (created second) ends up active, matching the spec's stated
    # preference of "Postgres preferred if both are present".
    assert created[-1][1] == "postgresql"


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


def test_migrate_legacy_source_migrates_postgres_only(monkeypatch):
    """A user with only a legacy Postgres connection (no Inflectiv key) still
    gets exactly one data_sources row created, same as before this fix."""
    created = []
    monkeypatch.setattr(routes_app.store, "list_for_user", lambda uid: [])
    monkeypatch.setattr(routes_app.store, "create", lambda *a, **kw: created.append(a) or {"id": 3})
    monkeypatch.setattr(routes_app.db_connector, "list_tables_light",
                         lambda cs: [{"table_name": "t", "row_estimate": 1, "columns": [], "foreign_keys": []}])

    async def fake_build_table_index(light):
        return light

    monkeypatch.setattr(routes_app, "build_table_index", fake_build_table_index)

    user = {"id": 1, "db_connection_string": "postgresql://x", "db_table_name": "t", "inflectiv_key": None}
    routes_app._migrate_legacy_source(user)

    assert len(created) == 1
    assert created[0][1] == "postgresql"


def test_migrate_legacy_source_called_at_signup_time_migrates_both(monkeypatch):
    """Fix 2: _migrate_legacy_source is now also called from signup(), using the
    freshly-inserted `INSERT ... RETURNING *` user row shape. This exercises
    that exact shape (both legacy fields populated straight from signup,
    before any login has ever happened) to confirm a brand-new dual-source
    signup gets both data_sources rows immediately, with Postgres active -
    matching what the Datasets tab should show right after signup, with no
    separate login required."""
    created = []
    monkeypatch.setattr(routes_app.store, "list_for_user", lambda uid: [])
    monkeypatch.setattr(routes_app.store, "create", lambda *a, **kw: created.append(a) or {"id": 1})
    monkeypatch.setattr(routes_app.db_connector, "list_tables_light",
                         lambda cs: [{"table_name": "t", "row_estimate": 1, "columns": [], "foreign_keys": []}])

    async def fake_build_table_index(light):
        return light

    monkeypatch.setattr(routes_app, "build_table_index", fake_build_table_index)

    # Shape of the row returned by signup()'s `INSERT ... RETURNING *`.
    freshly_signed_up_user = {
        "id": 42, "email": "new@user.com", "name": "New User", "company": None,
        "pw_hash": "x", "pw_salt": "y", "api_token": "tok",
        "inflectiv_key": "k", "inflectiv_dataset_id": 5, "inflectiv_dataset_name": "ds",
        "db_type": "postgresql", "db_connection_string": "postgresql://x", "db_table_name": "t",
        "onboarding": {}, "ai_prefs": {},
    }
    routes_app._migrate_legacy_source(freshly_signed_up_user)

    assert len(created) == 2
    assert created[0][1] == "inflectiv"
    assert created[1][1] == "postgresql"


def test_migrate_legacy_source_noop_for_fresh_user(monkeypatch):
    monkeypatch.setattr(routes_app.store, "list_for_user", lambda uid: [])
    calls = []
    monkeypatch.setattr(routes_app.store, "create", lambda *a, **kw: calls.append(a))
    routes_app._migrate_legacy_source({"id": 1, "db_connection_string": None, "inflectiv_key": None})
    assert calls == []


def test_migrate_legacy_source_swallows_db_source_failure(monkeypatch):
    monkeypatch.setattr(routes_app.store, "list_for_user", lambda uid: [])

    def boom(cs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(routes_app.db_connector, "list_tables_light", boom)

    user = {"id": 1, "db_connection_string": "postgresql://unreachable",
            "db_table_name": "t", "inflectiv_key": None}
    # Should not raise, even though _migrate_db_source blows up internally.
    routes_app._migrate_legacy_source(user)
