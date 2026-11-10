# Agents that learn from their own mistakes: leadership update (draft outline)

Structure follows Nancy Duarte's *Resonate*: open with **what is**, move between **what is**
and **what could be**, turn on a **call to adventure** and a **call to action**, and end on
the **new bliss**. Leadership is the hero (they decide what happens next); the presenter is
the mentor (brings the insight, the evidence and a plan). One **STAR moment**, something
they'll always remember, sits in the middle.

**Big Idea** (one sentence, point of view + stakes):
> Our coding agents repeat the same mistakes in every session, and we can make them learn
> from each other across every tool, at the proxy we already run, with no extra work for
> developers. If we don't, we keep paying for the same mistakes at the scale we adopt agents.

Length: 16 slides, 20 minutes plus discussion.

---

## Beginning: what is

### 1. Title
- "Agents that learn from their own mistakes"
- Subtitle: Cortex + Evolve, work in progress
- *Say:* this is early; I want your read on direction, not sign-off on a product.

### 2. What is: every session starts from zero
- Coding agents (Claude Code, Codex, Bob, opencode) don't remember what went wrong
  yesterday, or what another developer's agent already discovered.
- Every repository has rules no prompt mentions: how to run tests, generate code, version a
  client, word a commit.
- Today teams write these down by hand in instruction files, if at all, and they go stale.
- *Image:* one real timeline of an agent's commit rejected by a repo's hooks, then fixed.
- *Say:* this happens in every session, for every developer, on every agent.

### 3. What is: what that costs us
- Repeated wasted steps: tool calls, tokens, minutes per task.
- Rework and review load when an agent gets a convention wrong.
- Knowledge stays in individual sessions and never compounds.
- *Say:* the cost grows linearly with agent adoption. That's the stakes.
- *Note:* no org-wide numbers yet; frame qualitatively, or with one illustrative session
  (the best recorded pair spent 56 tool calls and $0.32 where the guided run spent 41 and $0.19).

---

## Turning point 1: call to adventure

### 4. What could be: learn once, apply everywhere
- An agent hits a problem, works it out, and from then on **every agent, in every session,
  starts already knowing**.
- Without anyone writing anything down, grading anything, or changing how developers work.
- *Say:* this is the shift: from agents that are smart to agents that get smarter here.

---

## Middle: alternate between what is and what could be

### 5. Why now, and why us: we already sit in the right place
- **Cortex** already sits in front of every coding agent, sees every request and tool result.
- **Evolve** already turns agent sessions into reusable guidelines.
- Joining them closes the loop: Cortex records → Evolve learns → a person reviews → Cortex
  injects into the next session.
- *Diagram:* the five-step loop (from the example walkthrough).
- Contrast: *is* "each agent vendor's own memory, per tool"; *could be* "one learning layer
  across all agents we use".

### 6. How it works, in one picture (low on detail)
- A plugin inside Cortex; no changes to the agents, and no core Cortex changes.
- Fails open: if Evolve is down, requests go through unchanged.
- A review gate: nothing learned is used until a person approves it, so text from a
  malicious web page can't become a permanent instruction.
- *Say:* built to be safe to try: inert until turned on, removable with one config line.

### 7. What we learned first: the delivery surprise *(STAR moment)*
- We gave agents a correct guideline. It made **no difference**: 6 of 11 runs still made
  the mistake, no better than no guideline (3 of 6).
- We moved the same words from the end of the system prompt into the user's first message.
  The mistake disappeared: **0 of 11**.
- *Say:* same words, different place. Where you put knowledge matters as much as the
  knowledge. Most "AI memory" efforts would have stopped at "it doesn't work".
- *Visual:* two bars, 6/11 → 0/11, nothing else on the slide.

### 8. What could be, shown: one lesson end to end
- Walk the worked example (`demos/cortex-repo/example-walkthrough.html`):
  1. The agent's commit is rejected twice by the repo's hooks.
  2. Evolve learns, with no grading: "read all hook requirements before committing".
  3. A person approves it.
  4. A new session doing a different change reads the hooks first and commits first time.
- Numbers for that pair: rejected commits 2 → 0, tool calls 56 → 41, cost $0.32 → $0.19.
- *Say:* this is the best pair. Next slide is the honest average.

### 9. What is, honestly: the evidence so far
| Experiment | Result |
|---|---|
| Delivery (28 sessions) | Wrong placement: no effect. Right placement: mistake eliminated (6/11 → 0/11) |
| Repository conventions (6 pairs) | Commits right first time 0/6 → 2/6; rejected commits 12 → 7; cost flat |
| Live run, Nov 9 | Baseline rejected once; guided committed first time |
| Cross-agent, Claude Code → Codex (1 pair) | Codex rejected commits 2 → 0; tool calls 13 → 8; input tokens −39% |
| Billing workflow (6 cases, with feedback) | Correct results 2/6 → 4/6 |
| Invoice extraction | No gain: agents already got it right, nothing to learn |
- *Say:* direction is consistent; size is not yet established; small samples.
- *Say:* the invoice result taught us which tasks benefit: ones with local knowledge the
  model can't guess.

### 10. What could be, across vendors: learned from Claude Code, used by Codex
- Claude Code (Sonnet 4.6) is rejected by the repo's hooks; Evolve learns "update CHANGES.md and
  the client version before the first commit"; Codex (gpt-5.6-luna), a different vendor and model,
  does a different change.
- Codex rejected commits 2 → 0, tool calls 13 → 8, input tokens 182k → 110k (−39%). One pair.
- *Say:* this is the answer to "won't the vendors do this?": a layer we control, working across two of them.
- Report: `demos/cortex-repo/reports/codex-transfer-20261109/report.html`

### 11. Resistance: the questions you're probably asking
Handle objections before they're raised (Duarte: address resistance in the middle).
- **"Won't the vendors do this?"** Each vendor learns within its own tool; we use several,
  and our conventions are ours. A layer we control works across all of them.
- **"Is it safe?"** Review gate, fails open, local only today; the open security items are
  listed, not hidden (unauthenticated local service, no redaction yet).
- **"Is the gain real?"** Directionally yes, statistically not yet; slide 15 is how we find out.
- **"Does it need developer effort?"** No feedback, no grading, no prompt changes. A person
  approves guidelines, and even that may become automatic.

### 12. What could be, at scale
- Every repository's conventions, learned from the agents' own sessions, kept current.
- New hires' agents behave like a veteran's on day one.
- Fewer wasted steps and rejected changes across every agent we license.
- *Say:* the more we use agents, the more each session pays back.

### 13. What could be: what only a layer in front of every agent can do
- Cross-agent transfer is one of several things a vendor's own memory can't do. Cortex sees
  every agent, model and user, plus their tool results, costs and policy decisions.
- Eight cards: across agents (shown), **across models**, across people, within a workflow,
  from what Cortex blocked, from waste, **measured automatically**, kept current and local.
- *Say:* the two I'd do next are across models (a cost number: a strong model teaches, a cheap
  one works) and measured automatically (answers "is the gain real?" from real sessions).
  Learning from what Cortex blocked is the one for the Cortex team. None adds developer effort.
- Details: `demos/cortex-repo/angles.html`

---

## Turning point 2: call to action

### 14. Where we are: rough edges, stated plainly
| Works | Partly | Not yet |
|---|---|---|
| Record, learn, review, inject loop end to end | Guidelines are generic and reactive; they say "how to recover", not "the rule" | Automatic promotion with an A/B safety check |
| Plugin isolated, fails open, review gate | Measured on synthetic tasks, small samples | Real repositories, real teams |
| Two Evolve fixes found and made along the way | Review is a command-line step | UI in agentop; redaction; authentication |
- Open design questions to decide with the Cortex team: manual vs automatic review, where
  review lives, when sessions are learned from, how often guidelines are retrieved.

### 15. The ask
Pick two or three; be specific about people and weeks.
- **A pilot:** two or three real repositories, opted-in teams, four weeks, with an A/B
  comparison so we measure rejected changes, wasted steps and cost per task.
- **Cortex team alignment** on the plugin and the open design questions (meeting held /
  scheduled: <date>).
- **One engineer for a quarter** to make guidelines repository-specific (the change most
  likely to turn "modest" into "clear") and to add the A/B gate.
- **A decision owner** for the security items before anything leaves a laptop.

---

## End: the new bliss

### 16. The future we're building
- Come back to slide 2's timeline, now with the guided version beside it: the rejected
  commit is gone.
- "Every mistake an agent makes here, it makes once."
- *Close:* restate the Big Idea and the ask in one breath.

---

## Delivery notes

- **Audience is the hero.** Frame decisions as theirs: "you can choose to pilot this".
- **Contrast is the engine.** Each middle slide should feel like a swing between today's
  friction and the alternative; if a slide doesn't swing, cut it.
- **One STAR moment.** Slide 7. Pause after the 0/11; don't add more data to that slide.
- **Honesty earns trust.** Show the best pair, then immediately the average and the failure
  (invoices). Leadership will trust the 2/6 more because you showed it.
- **Backup slides** (not presented): angles in detail (`demos/cortex-repo/angles.html`), integration review details
  (`demos/cortex-repo/integration-review.html`), full experiment report
  (`demos/cortex-repo/reports/sonnet45-20261106/report.html`), placement experiment
  (`demos/cortex-invoices/placement.md`), live run timelines.
- **Live demo:** don't. Show recorded timelines; offer the hands-on walkthrough afterwards.
