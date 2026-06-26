# IDEAS — Current Useful Extensions

This is the live idea scratchpad. Historical notes that no longer describe the
current system live in `docs/archive/`.

## Budget Rule

Homunculus is designed around a roughly $5/month operating budget. Prefer:

- deterministic checks over extra model calls;
- piggybacking on the existing daily reflection tick over per-turn analysis;
- read-only integrations before mutating integrations;
- human-gated proposals for deletion, skill rewrites, and broad behavior change.

## In This Branch

- Memory consolidation proposals: deterministic duplicate/stale-memory scan,
  with every deletion routed through the existing proposal review queue.
- Traces run cards: grouped recent chat/heartbeat runs on the Traces page,
  backed by the existing event log/replay builder.
- Skill contract tests: CI-friendly structural checks for live skill files,
  including filename/name matching and missing tool references.

## Ideas I Would Recommend From First Principles

1. **Personal context integrations, read-only first**
   Calendar, inbox, location, weather, and code-hosting summaries make the agent
   useful because they ground it in the user's actual day. Start read-only and
   audit every access.

2. **Reliable capture**
   Phone shortcut, share-sheet, browser extension, and command-line capture are
   more valuable than another chat UI. The best assistant is the one you can
   tell things to at the moment they appear.

3. **Commitment ledger**
   Track promises, open loops, deadlines, and “ask me later” moments. Extract in
   the existing reflection tick so usefulness improves without a new per-turn
   cost.

4. **Human usefulness feedback**
   Add a tiny useful/not-useful signal on deliveries. Deterministic task success
   says “it ran”; human feedback says “it mattered.”

5. **Inspectable autonomy**
   Every autonomous action should be answerable: why did it run, what tools did
   it use, what did it cost, what guard accepted it, and what changed.

6. **Small action approvals**
   For higher-risk actions like sending email, editing files, or browser login,
   generate drafts and approval requests instead of acting directly.

7. **Skill contracts**
   Treat procedural memory like code: validate required tools, expected states,
   success criteria, and fixture-style scenarios before a skill is allowed to
   run unattended.

8. **Memory hygiene**
   Keep memory useful by proposing merges/deletes for stale or duplicate
   entries. Never silently delete user/profile memories.

9. **Reachability**
   Make notification delivery channel-agnostic: web feed baseline, push when
   available, Discord/Telegram as relays, and no single point of failure.

10. **Cost visibility**
    Surface cost per run and per day wherever autonomy is visible. The system
    should make the cheap path obvious.
