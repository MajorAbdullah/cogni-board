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
