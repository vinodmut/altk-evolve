"""Write guidelines from a JSON file into an Evolve namespace.

For demos that start after learning: seed the guidelines a session would have produced,
then run the agents. Uses the Evolve settings in the environment, so source the same
evolve.env as the Evolve server first. The server reads the backend afresh, so it serves
the new guidelines without a restart.

    set -a; . "$WALK/evolve.env"; set +a
    uv run python demos/cortex-repo/seed_guidelines.py demos/cortex-repo/guidelines/rules-20261111.json

The file is either a list or an object with a "guidelines" list. Each entry is a string
(the guideline text) or an object with "content" and optional "metadata". Retrieval ranks
guidelines by how similar their source task is to the new task, so keep
metadata.task_description.

By default the namespace (cortex, the one the Cortex plugin serves) must have no
guidelines yet: --replace deletes the ones it has first, --append adds to them.
"""

import argparse
import json
import sys
from pathlib import Path

# Metadata Evolve computes on write; a copy taken from another store would be stale.
_COMPUTED = {"memory_revision", "provenance_incomplete", "processing", "sources", "support"}


def load(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    rows = data["guidelines"] if isinstance(data, dict) else data
    out = []
    for row in rows:
        row = {"content": row} if isinstance(row, str) else row
        if not str(row.get("content", "")).strip():
            raise SystemExit(f"{path}: every guideline needs content")
        metadata = {k: v for k, v in (row.get("metadata") or {}).items() if k not in _COMPUTED}
        out.append({"content": row["content"].strip(), "metadata": metadata})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("file", type=Path, help="JSON file of guidelines")
    parser.add_argument("--namespace", default="cortex", help="namespace to write to (default: cortex)")
    existing = parser.add_mutually_exclusive_group()
    existing.add_argument("--replace", action="store_true", help="delete the namespace's guidelines first")
    existing.add_argument("--append", action="store_true", help="keep the namespace's guidelines and add these")
    args = parser.parse_args()

    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.schema.core import Entity

    rows = load(args.file)
    client = EvolveClient()
    client.ensure_namespace(args.namespace)
    current = client.get_all_entities(args.namespace, filters={"type": "guideline"}, limit=10000)
    if current and not (args.replace or args.append):
        print(f"{args.namespace} already has {len(current)} guidelines; use --replace or --append.", file=sys.stderr)
        return 1
    if args.replace:
        for entity in current:
            client.delete_entity_by_id(args.namespace, entity.id)
        print(f"deleted {len(current)} guidelines from {args.namespace}")

    entities = [
        Entity(type="guideline", content=row["content"], metadata={**row["metadata"], "status": "active", "seeded_from": args.file.name})
        for row in rows
    ]
    updates = client.update_entities(args.namespace, entities, enable_conflict_resolution=False)
    for update, row in zip(updates, rows):
        print(f"{args.namespace} #{update.id}: {row['content'][:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
