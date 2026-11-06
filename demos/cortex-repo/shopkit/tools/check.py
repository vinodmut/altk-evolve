"""Pre-merge checks: generated code is current, and migrations match the schema."""

import sys

import migrate
import schema

models = schema.load()
problems = []
for path, render in (
    (schema.ROOT / "shopkit" / "models.py", schema.render_models),
    (schema.ROOT / "clients" / "python" / "shopkit_client.py", schema.render_client),
):
    if path.read_text() != render(models):
        problems.append(f"{path.relative_to(schema.ROOT)} is stale: run `make generate`")
have = migrate.database_columns(migrate.apply(":memory:"))
for table, wanted in schema.columns(models).items():
    missing = [column for column in wanted if column not in have.get(table, {})]
    if missing:
        problems.append(f"migrations do not create {table}.{', '.join(missing)}: run `make migration NAME=<change>`")
for problem in problems:
    print("check failed:", problem)
if problems:
    sys.exit(1)
print("check passed: generated code and migrations match schema/models.yaml")
