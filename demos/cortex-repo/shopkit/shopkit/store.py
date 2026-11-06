"""SQLite persistence for the generated models."""

import sqlite3
from dataclasses import asdict, fields

from shopkit.models import TABLES

MODELS = {model: table for table, model in TABLES.items()}


def connect(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def insert(connection, record):
    values = {key: value for key, value in asdict(record).items() if value is not None}
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    cursor = connection.execute(f"INSERT INTO {MODELS[type(record)]} ({columns}) VALUES ({marks})", list(values.values()))
    connection.commit()
    return cursor.lastrowid


def get(connection, model, record_id):
    row = connection.execute(f"SELECT * FROM {MODELS[model]} WHERE id = ?", (record_id,)).fetchone()
    if row is None:
        return None
    return model(**{field.name: row[field.name] for field in fields(model)})


def update(connection, record):
    values = {key: value for key, value in asdict(record).items() if key != "id"}
    assignments = ", ".join(f"{key} = ?" for key in values)
    connection.execute(f"UPDATE {MODELS[type(record)]} SET {assignments} WHERE id = ?", [*values.values(), record.id])
    connection.commit()
