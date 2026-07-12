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
