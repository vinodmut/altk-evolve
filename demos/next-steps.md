# Cortex + Evolve: review and next steps

Written 2026-11-05 after reviewing Cortex `evolve-plugin` (`core/plugins/evolve`), this
branch's Cortex sync, the [photo demo](cortex-photo/readme.md), the
[invoice demo](cortex-invoices/readme.md), and the billing/ledger experiments in
[experiments/cortex_workflow_learning](../experiments/cortex_workflow_learning/README.md).

## Is the integration architecturally sound?

Mostly yes. Capturing and injecting at the proxy makes the loop agent-neutral (Claude Code,
Codex, Bob, opencode) with no agent-side changes. The plugin fails open, keeps the injected
block byte-stable for prompt caching, uses only Evolve's stock MCP tools, separates learned
(`cortex-candidates`) from served (`cortex`) guidelines as a review gate, and makes resent
batches idempotent.

Gaps:

1. **No outcome reaches the learner.** The plugin calls `process_trajectory` without an
   outcome, so `generate_guidelines.jinja2` takes its "no evaluation; the trajectory may
   contain the agent's self-evaluation" branch. The agent always reports success, so the
   learner mostly writes down how a successful run went.
2. **No effect feedback.** Cortex records which guideline ids shaped each request, but
   nothing returns to Evolve. Guidelines cannot be scored, retired, or ranked by whether
   they helped. Shadow A/B gating (listed under "Not yet" in Cortex's `evolve-plugin.md`)
   closes this.
3. **`injected: true` does not prove the model saw the guidance.** The demo gateway drops
   later Anthropic system blocks (worked around with the bridge), and even when delivered,
   the block sits at the end of Claude Code's very large system prompt, the weakest
   position for steering.
4. **Two session → trajectory converters.** The Go recorder in the plugin
   (`trajectory.go`) and `altk_evolve/sync/cortex_sync.py` (plus the earlier REST endpoint)
   do the same job. Every demo learns through the Python pull path, so the plugin's own
   idle-learning path is not what has been evaluated. Pick one.
5. **Learner-side context loss.** PDF pages and images the agent read are dropped from the
   recorded trajectory, so the learner cannot see what the agent saw.

## Why the demos show no improvement yet

1. **No headroom.** Every invoice baseline in the consistency and standard sets passed.
   Training passed on the first try, and its feedback said all 16 rows matched. Evolve's
   prompt says to return an empty list when a task succeeds without struggle. What came
   back was "use `Decimal`" (the model already does) and "embed the extracted data in the
   script" (codifies hand transcription).
2. **The one real recurring error was misdiagnosed, and the guideline did not change
   it.** Most runs first read `/tmp/tmp.XXXX/input/*.pdf`, get three errors, run `pwd`,
   and retry. That path appears nowhere in the request (system prompt, tools, or
   messages); Sonnet invents it. Wrong-path reads: standard set 2/3 baseline vs 3/3
   guided; consistency set 3/3 vs 3/3, with the guideline delivered
   (`body_rewritten`, first request 4,873 → 5,507 bytes). The learned guideline says "use
   absolute paths from the current working directory", but the model *was* using absolute
   paths, just invented ones. The real lesson is "the working directory is the stated
   `CWD`; never guess a temporary directory."
3. **Photo task.** The same headroom problem, plus a transfer gap from GPS export to
   focal length. Pillow is the default habit and won in 5 of 6 attempts.
4. **Too few pairs.** Calls on identical PDFs ranged from 12 to 68. Three pairs cannot
   resolve an effect of this size.
5. **The one positive result fits the pattern.** The ledger experiment (Evolve 4/6 vs
   baseline 2/6; 85 vs 124 turns) had a baseline that genuinely failed, a task that
   depended on environment-specific knowledge the model could not infer (contract clauses,
   CLI help), and an explicit failure signal for learning.

## Recommendations

1. **Isolate guideline quality from delivery.** Serve a hand-written, correct `CWD`
   guideline and run paired baseline/guided sessions on identical invoices. If wrong-path
   reads disappear, the bottleneck is guideline quality (Evolve's root-cause analysis and
   filtering out advice the model already follows). If they persist, it is delivery and
   placement (Cortex). Wrong-path reads are a cheap, objective metric.
   *Status: in progress; see [cortex-invoices/placement.md](cortex-invoices/placement.md).*
2. **Cortex: inject where the model pays attention.** Compare the current trailing system
   block with a `<system-reminder>` attached to the first user message, which Claude Code
   models are trained to heed. The block stays byte-stable across turns, so prompt caching
   is unaffected. Later: just-in-time injection, adding a matching guideline on the next
   request after a tool error that fits its trigger. Cortex already sees every tool result.
   *Status: in progress, after 1.*
3. **Outcome signals.** *Deferred by decision: the loop must work without extra user
   overhead.* If revisited, use only signals that need no user involvement: tool errors and
   retries, in-session self-corrections, and contrasting repeated runs of the same task
   (for example the 12-call and 68-call runs on identical PDFs). An error→recovery learning
   mode for runs that ultimately succeeded fits this constraint.
4. **Choose a demo task that has headroom:**
   - the baseline fails at least half the time, for a deterministic reason;
   - the cause is environment knowledge the model cannot infer (an internal CLI quirk, a
     required flag, a pagination cursor, a repo convention, contract clauses);
   - the lesson is one sentence and transfers to held-out variants;
   - an automatic grader can check it.

   The ledger task already meets these; turn it into a Docker demo like the photo one and
   run at least 10 pairs. Invoice extraction mostly tests the model's reading ability, not
   missing knowledge.
5. **Change the model only after that.** Haiku 4.5 as the task model with a stronger
   learner adds headroom and makes a good story ("Haiku with Evolve matches Sonnet"), but a
   weaker model will not help on a task that does not need learned knowledge.
6. **Measure properly:** at least 10 pairs per arm, pass rate and cost per success, with
   the `/tmp` noise either fixed or reported separately.
