"""Database migrations: apply them to a SQLite file, or create one for a schema change.

python tools/migrate.py apply DB
python tools/migrate.py new NAME
"""

import re
import sqlite3
import sys

import schema

MIGRATIONS = schema.ROOT / "migrations"


def files():
    return sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def apply(db):
    connection = sqlite3.connect(db)
    for path in files():
        connection.executescript(path.read_text())
    connection.commit()
    return connection


def database_columns(connection):
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    return {table: {row[1]: row[2] for row in connection.execute(f"PRAGMA table_info({table})")} for table in tables}


def new(name):
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise SystemExit("migration NAME must be lowercase letters, digits and underscores")
    current = database_columns(apply(":memory:"))
    statements = []
    for table, wanted in schema.columns(schema.load()).items():
        have = current.get(table)
        if have is None:
            body = ", ".join(f"{column} {kind}" + (" PRIMARY KEY" if column == "id" else "") for column, kind in wanted.items())
            statements.append(f"CREATE TABLE {table} ({body});")
            continue
        statements += [f"ALTER TABLE {table} ADD COLUMN {column} {kind};" for column, kind in wanted.items() if column not in have]
    if not statements:
        raise SystemExit("migration: the database already matches schema/models.yaml; nothing to do")
    number = int(files()[-1].name[:4]) + 1 if files() else 1
    path = MIGRATIONS / f"{number:04d}_{name}.sql"
    path.write_text("\n".join(statements) + "\n")
    print(f"created {path.relative_to(schema.ROOT)}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "apply":
        apply(sys.argv[2]).close()
    elif len(sys.argv) == 3 and sys.argv[1] == "new":
        new(sys.argv[2])
    else:
        raise SystemExit(__doc__)
