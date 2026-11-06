"""Grade one finished task repository: is the field really added, and is it committed?

Runs in a network-disabled container against a copy of the repository, so nothing the
agent left behind is changed. Prints a JSON verdict.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


def run(command, cwd, env=None):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    return result.returncode, (result.stdout + result.stderr)[-2000:]


def grade(repo, task):
    checks = {}
    work = Path(tempfile.mkdtemp()) / "repo"
    shutil.copytree(repo, work, ignore=shutil.ignore_patterns("*.db", "__pycache__", ".pytest_cache"))
    code, _ = run(["git", "status", "--porcelain", "--untracked-files=all"], work)
    status = run(["git", "status", "--porcelain", "--untracked-files=all"], work)[1].strip()
    checks["committed"] = code == 0 and not status
    commits = run(["git", "rev-list", "--count", "HEAD"], work)[1].strip()
    checks["new_commit"] = commits.isdigit() and int(commits) > 1
    checks["pre_merge_check"] = run(["python3", "tools/check.py"], work)[0] == 0
    db = str(work / "grade.db")
    migrated = run(["python3", "tools/migrate.py", "apply", db], work)[0] == 0
    checks["migrations_apply"] = migrated
    model, field, value = task["model"], task["field"], task["value"]
    probe = f"""
import json, sys
from dataclasses import fields
sys.path.insert(0, "clients/python")
from shopkit import store, models
import shopkit_client
model = getattr(models, {model!r})
assert {field!r} in [f.name for f in fields(model)], "model lacks field"
shopkit_client.payload({model!r}, {field}={value!r})
connection = store.connect({db!r})
user_id = store.insert(connection, models.User(id=None, email="grader@example.com", name="Grader"))
base = {{"User": dict(email="g2@example.com", name="G2"), "Order": dict(user_id=user_id, total_cents=500),
         "Product": dict(sku="GRADE-1", title="Grade", price_cents=100)}}[{model!r}]
record_id = store.insert(connection, model(id=None, **base, {field}={value!r}))
print(json.dumps(getattr(store.get(connection, model, record_id), {field!r})))
"""
    code, output = run(["python3", "-c", probe], work)
    checks["round_trip"] = code == 0 and output.strip().splitlines()[-1:] == [json.dumps(value)] if code == 0 else False
    if migrated:
        table = {"User": "users", "Order": "orders", "Product": "products"}[model]
        columns = [row[1] for row in sqlite3.connect(db).execute(f"PRAGMA table_info({table})")]
        checks["column_migrated"] = field in columns
    else:
        checks["column_migrated"] = False
    env = {**os.environ, "PATH": os.environ.get("PATH", "")}
    checks["tests_pass"] = run(["./scripts/test"], work, env)[0] == 0
    changes = (work / "CHANGES.md").read_text() if (work / "CHANGES.md").exists() else ""
    unreleased = changes.split("## Unreleased", 1)[-1].split("\n## ", 1)[0]
    checks["changelog"] = field in unreleased
    root = run(["git", "rev-list", "--max-parents=0", "HEAD"], work)[1].strip()
    changed = run(["git", "diff", "--name-only", root, "HEAD"], work)[1].split()
    checks["client_version_bumped"] = "clients/python/VERSION" in changed
    subjects = run(["git", "log", "--format=%s", f"{root}..HEAD"], work)[1].splitlines()
    checks["conventional_commits"] = bool(subjects) and all(
        re.match(r"^(feat|fix|chore|docs|refactor|test)(\([a-z0-9-]+\))?: [a-z]", s) for s in subjects
    )
    checks["probe_output"] = output.strip()[-400:]
    passed = all(value for key, value in checks.items() if key != "probe_output")
    return {"passed": passed, "checks": checks}


if __name__ == "__main__":
    repo, task = Path(sys.argv[1]), json.loads(sys.argv[2])
    print(json.dumps(grade(repo, task)))
