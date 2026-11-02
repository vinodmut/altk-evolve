"""Local, synthetic SimpleNote-style task tool; all operations are read-only."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search", help="Find notes by title; returns every match, including archived notes")
    search.add_argument("query")
    read = sub.add_parser("read", help="Read one note, including its archive status and content")
    read.add_argument("note_id")
    args = parser.parse_args()
    notes = json.loads(Path(__file__).with_name("notes.json").read_text())
    if args.command == "search":
        result = [{k: n[k] for k in ("id", "title", "archived")} for n in notes if args.query.lower() in n["title"].lower()]
    else:
        result = next((n for n in notes if n["id"] == args.note_id), {"error": "note not found"})
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
