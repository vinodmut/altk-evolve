# Repository conventions, Sonnet 4.5 (2026-11-06)

Task and learner `aws/claude-sonnet-4-5`; Claude Code 2.1.287 in batch mode; Cortex
`evolve-plugin` at `ab08dda3` with `placement: user`. One training session, six interleaved
baseline/guided pairs over three held-out tasks (the arm that runs first alternates).

| | Baseline | With Evolve |
|---|---:|---:|
| Correct and committed (grader) | 6/6 | 6/6 |
| Commit accepted on the first try | **0/6** | **2/6** |
| Rejected commits, total | 12 | 7 |
| Runs rejected for a missing CHANGES.md entry | 6/6 | 2/6 |
| Runs rejected for a missing client VERSION bump | 6/6 | 4/6 |
| Runs rejected for the commit subject format | 6/6 | 3/6 |
| Tool calls, median | 42.5 | 44 |
| Cost per run, mean | $0.237 | $0.229 |

**The direction is right, the effect is modest.** Every baseline committed exactly the same
way: one rejection for changelog plus version, a second for the commit subject. Two guided
runs (guided-3, guided-5) read `.githooks/` before committing and were accepted first time;
the other four still hit one or two rejections. Correctness tied because the hooks make every
run fix its commit before finishing. Calls and cost are flat: the guided runs that avoided
rejections spent the saved calls reading the hooks instead.

## What Evolve learned, with no feedback

From the training session's own commit rejections (four promoted unchanged, one not):

1. For code generation systems, always modify the source schema first, then run the generation
   tool, rather than manually editing generated files.
2. When commit hooks reject a commit, read the error message to identify all required companion
   changes (changelog, version bumps, commit format) before re-attempting.
3. For schema changes that add database columns, use the repository's migration tool to
   auto-generate the migration rather than writing SQL manually.
4. After making changes to a schema-driven system, run the repository's consistency check tool
   before committing to catch mismatches between schema and derived artifacts.

Not promoted: generic semantic-versioning advice (see [review.json](review.json)).

## Why the effect is not larger

- **The learned lessons are reactive and generic.** Guideline 2 says what to do *after* a
  rejection. It does not state the rules themselves ("a schema change needs a CHANGES.md entry
  under Unreleased, a clients/python/VERSION bump, and a lower-case Conventional Commit
  subject"), so most guided runs still had to be rejected once to learn them. Evolve's prompt
  asks guidelines to generalise and to leave out task-specific values, which pushes it away
  from exactly the repository-specific facts that would help here.
- **The first learning pass missed the commit lessons entirely.** At Evolve's default 50-step
  window the session was cut to 50 of its 61 steps, losing the second rejection and the
  recovery that followed; the first
  candidates (kept in [superseded/](superseded/)) said nothing about hooks. The demo now
  renders 150 steps.
- **Six pairs are a small sample**, with no correctness headroom on this task.

## Next

- Let Evolve keep scoped, concrete facts: guidelines tied to a repository (or Cortex working
  directory) can name its files and rules, instead of being generalised away. Then test whether
  guided runs commit first time.
- Raise the default trajectory window for Cortex coding sessions, or render error→recovery
  steps first, so late lessons are not truncated.
