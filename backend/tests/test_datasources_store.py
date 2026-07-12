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
