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
