# claude-autohealth — future work

Forward-looking directions that aren't scoped as phases yet. Decision
criteria kept at the source document so items surface at the right time.

---

## v6. Thinking-block signal extraction (contradictory-constraint detector)

**Status:** deferred — **gate 1 failed 2026-04-21**. See
`docs/decisions/0001-v5-ab-null-result.md`. v5 A/B analysis shows
treatment and control are indistinguishable (health-delta Δ=+0.006,
step+10 health Δ=−0.004, session recovery tied at 96%). No v6 work
starts until v5's causal story is fixed or a different justification
emerges.

**Premise:** v5 signals catch oscillation (compression, file revisit,
error rate, diversity) but not the *diagnosis* moment where Claude
realizes two constraints contradict. That lives in `<thinking>` prose,
not in tool args. The 7 loop-trigger experiments (2026-03-25) established
contradictory edit-test-edit as the actual loop trigger; v6 would catch
it earlier.

**Shape:** event-anchored extraction (CVR-style: markers + ±1 turn
context), not block summarization. Log-only for ≥2 weeks before
weighting. Reuse existing A/B harness and trace store — no parallel
pipeline.

**Design document + provenance + gating criteria:**
`~/devel/pa/mnemex/IDEAS.md` → section **N**.

The PA-side entry is the canonical source because it includes the
full provenance (first-principles deconstruction, lateral-shift
synthesis, empirical reconciliation against v5 trace data) and the
"what the original framing got wrong" postmortem citing v5's dropped
`blind_retry` / `null_edit` signals as the direct analog failure mode
to avoid.

**Do not start** until:

1. ~~v5 A/B analysis demonstrates causal recovery vs. control.~~
   **FAILED 2026-04-21** (n=258 sessions, 1,246 warnings; T vs C
   indistinguishable). Unblocker: fix v5's causal story first — either
   narrower signals, louder/different warning wording, or retire the
   live nudge and keep signals as passive annotation.
2. Contradictory-constraint loops confirmed as a distinct failure mode
   (not a relabel of existing mechanics) via qualitative tagging of
   ~20 v5-flagged sessions.
3. A deterministic marker set achieves 0.5%–5% firing rate against
   the existing trace store in log-only mode for 2 weeks.
