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
