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
