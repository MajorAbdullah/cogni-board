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
