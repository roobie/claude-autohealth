# 0001 — v5 warning delivery shows no causal recovery effect

**Date:** 2026-04-21
**Status:** accepted (result)

## Context

Autohealth v5 shipped warnings behind a per-session A/B harness: sessions
are split on the last hex char of the session UUID into `treatment`
(warning delivered) and `control` (warning logged, not delivered). The
hypothesis was that delivering "you're looping" warnings would shorten
loops and raise subsequent health relative to control. Analysis was
originally planned for ~2026-04-14.

This record captures the 2026-04-21 analysis run against
`~/.claude/hooks/autohealth-trace.jsonl` (100,556 entries, 258 labeled
sessions with ≥1 warning: 147 T / 111 C, 1,246 total warnings).

Run: `mise x -- uv run ~/devel/pa/scripts/autohealth-ab-analyze.py`

## Result

Treatment and control arms are indistinguishable within noise.

| Metric                          | Treatment | Control | Δ      |
|---------------------------------|-----------|---------|--------|
| Health delta per warning (mean) | −0.033    | −0.039  | +0.006 |
| Improved (>+0.03) after warning | 27%       | 28%     | −1pp   |
| Worsened (<−0.03) after warning | 51%       | 54%     | −3pp   |
| Recovered ≥0.65 after warning   | 41%       | 38%     | +3pp   |
| Session-level recovery          | 96%       | 96%     | 0      |
| Health at step+10 post-warning  | 0.668     | 0.672   | −0.004 |

Post-warning health trajectories T vs C track within 0.005 at every
offset checked (steps +1, +3, +5, +10). The session-level 96% recovery
rate is identical across arms — sessions recover on their own; warning
delivery does not contribute.

## Decision

1. **Do not weight v5 warnings further.** They are not demonstrating
   benefit. Either ship a narrower/louder variant (v5.1) or retire the
   live nudge and keep the signals as passive annotations.
2. **v6 (thinking-block extraction) stays deferred.** IDEAS.md gating
   criterion #1 ("v5 A/B analysis demonstrates causal recovery vs.
   control") has failed. No v6 work starts until v5's causal story is
   fixed or a different justification for v6 emerges.
3. **Status quo on delivery is fine for now** — warnings do no harm in
   aggregate (no significant worsening vs. control), so no rollback
   urgency. But no expansion either.

## Consequences

- Blocks v6 entirely until the causal story is fixed.
- Opens the question of why 51% of warnings in treatment precede further
  worsening. Candidates: (a) signals fire too late — sessions are
  already past the point where a nudge matters; (b) the warning wording
  is wrong; (c) the 4-signal mechanic vocabulary misses the actual loop
  trigger (contradictory-constraint, per the 2026-03-25 experiments) and
  v5 is firing on correlates that don't carry the recovery lever.
- Interesting side-signal: "new tools introduced after warning" is 72%
  in control vs 65% in treatment. Small effect, opposite the intended
  direction — warning delivery may very slightly *suppress* exploration.
  Not load-bearing at this n; worth watching if v5.1 happens.
- Balance skew: 222 T vs 180 C total sessions (~55/45) instead of
  expected 50/50. Not alarming at this n but should be verified if the
  numbers grow meaningfully.

## References

- `~/devel/claude-autohealth/IDEAS.md` — v6 gating criteria (updated to
  mark gate-1 as failed).
- `~/devel/pa/mnemex/IDEAS.md` section N — canonical v6 design.
- `~/devel/pa/scripts/autohealth-ab-analyze.py` — analysis script.
- `~/.claude/hooks/autohealth-trace.jsonl` — trace data.
