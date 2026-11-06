# Cortex + Evolve: learning a repository's conventions

Every fresh Claude Code session on a repository rediscovers the same project rules the hard
way. Here Cortex records one ordinary session, Evolve learns from that session's own errors
(no grading, no feedback, nobody in the loop), and Cortex injects the reviewed guidelines
into later sessions doing *different* changes.

Everything runs in Docker: Claude Code, Cortex, Evolve, the sample repository and its
toolchain. The host needs Docker with Compose, a Cortex checkout, and API access.

## The sample repository

`shopkit/` is a small Python/SQLite library whose data model lives in `schema/models.yaml`.
Its git hooks reject a commit unless it has:

- regenerated code (`make generate`) and a migration (`make migration NAME=...`),
- an entry under `## Unreleased` in `CHANGES.md`,
- a bump of `clients/python/VERSION` whenever the client changes,
- a Conventional Commit subject in lower case (`feat(users): add nickname`).

Nothing in the task prompt mentions these. The README does not either; an agent finds them by
reading `.githooks/` or by being rejected.

Training adds `nickname` to users. Evaluation tasks add `discount_code` to orders,
`weight_grams` to products, and `locale` to users ([tasks.json](tasks.json)). A separate
network-disabled grader ([grade.py](grade.py)) checks the committed result: pre-merge check,
migrations, a stored and reloaded value, the test suite, changelog, client version and commit
subjects.

## First result (Sonnet 4.5, 6 pairs)

Open [reports/sonnet45-20261106/report.html](reports/sonnet45-20261106/report.html) for the
full report (charts, commit-by-commit comparison, every run), or read the short
[results.md](reports/sonnet45-20261106/results.md). Regenerate the HTML offline with
`uv run python demos/cortex-repo/report_html.py demos/cortex-repo/reports/sonnet45-20261106`.

## Run it

To hand this to someone else, send them [walkthrough.html](walkthrough.html): step-by-step instructions
with what to expect at each step, troubleshooting, and what to send back.

```bash
cd demos/cortex-repo
cp ../cortex-photo/.env.example .env     # set both model connections
export CORTEX_CHECKOUT=/path/to/cortex   # evolve-plugin, ab08dda3 or later
export DEMO_RESULTS_DIR="$PWD/results-my-run"
./demo setup                 # build, start, live API checks
./demo training              # one session; Cortex records it
./demo learn                 # Evolve learns from that session alone
./demo promote 1 2 3         # review the candidates, promote by ID
./demo pairs --count 6       # interleaved baseline/guided sessions
./demo report                # grade offline; open $DEMO_RESULTS_DIR/summary.html
./demo stop
```

`DEMO_PLACEMENT` defaults to `user` here: the guidelines go in a `<system-reminder>` on the
first user message, which the [placement experiment](../cortex-invoices/placement.md) found to
be the position the model acts on. Learning renders up to 150 steps of the session, because
the commit-hook lessons come at the end of runs longer than Evolve's default 50-step window.
