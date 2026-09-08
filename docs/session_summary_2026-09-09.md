# Session Summary — 2026-09-09

## Goal

Follow-up to yesterday's domestic title-match v2 work. The user asked to
investigate PRs #53/#55/#56 (an earlier attempt at international title-match
improvements that got fully reverted), then build a proper international v2
pipeline: separate `claude-sandbox` containers per market, restored
country-aware matching, and a fix for a known stale-confidence bug — this
time built so it can never cause the same cross-market regression that
caused the original revert.

## Investigation: PRs #53/#55/#56

PR #53 (→ `stage`) / #55 (→ `main`), both 2026-08-05, added four things for
international matching: a `country` field on the Vespa `movie_master_intl`
schema + query plumbing; an independent Bedrock Converse verification pass
(`rerank.py`); anniversary/re-release date-arithmetic rules inlined into the
prompt; and a fix for stale `confidence`/`decision`/`reasoning` after an id=0
post-lookup hit. PR #56 (2026-08-10) reverted the *entire* changeset as one
commit, because domestic accuracy regressed in production — the stale-
confidence fix lived in a code path **shared** between domestic and
international (`runner.py`'s post-lookup block), and the revert was done
wholesale to isolate the cause rather than partially. Two follow-ups were
named and never done: (a) fix the domestic bug in isolation, (b) rebuild
international as a fully separate module.

Verified against the current codebase before starting: the Vespa country
field, `rerank.py`, and the anniversary rules were all confirmed absent; the
stale-confidence bug was confirmed still present; international was
confirmed still running on the fully shared `runner.py`/`prompt_builder.py`.

## Decisions taken

- **International v2, fully standalone** — per PR #56's own unfulfilled
  recommendation (b). `runner_intl_v2.py` never calls `run_agentic_match`.
- **Reintroduce all three restorable pieces from PR #53/#55**: Vespa
  country-aware matching, the rerank verification pass, and the anniversary/
  re-release rules — user's explicit choice (multi-select) over doing only
  the country-matching piece.
- **The stale-confidence fix, done twice, independently**: domestic v2 gets
  its own copy (`post_lookup_v2.py`, confidence 0.75, never auto-accepts) and
  international v2 gets its own (`post_lookup_intl_v2.py`, confidence 0.90,
  can auto-accept) — no shared helper function between them. This is the
  direct lesson from the original regression: a shared code path for exactly
  this logic is what broke domestic last time.
- **Isolation is project-wide, not just for the confidence fix**: domestic
  v1 stays byte-identical; international v1 also stays untouched (confirmed
  explicitly, since the user's instruction only named domestic v1 but the
  same principle from the original domestic-v2 work applies).
- **Separate `claude-sandbox` containers per market** — user confirmed all
  three original drivers (concurrency isolation, different MCP tools/web-
  search locale, different model/settings defaults), so a second container
  (`claude-sandbox-intl`) was built for tooling/model differentiation.
- **Concurrency isolation was walked back after a deeper look.** The user
  initially accepted a 4-total/2+2-split semaphore partition, then asked to
  revisit it. On inspection, a semaphore partition alone doesn't actually
  guarantee isolation: worker *processes* aren't reserved per market, only
  semaphore *slots* are — if all workers are blocked waiting on one market's
  reduced-capacity semaphore, the other market's rows can't even be dequeued
  to reach their own (idle) slots. Raising total concurrency to 8 would have
  fixed this at the cost of ~2x Bedrock load; the user chose the third
  option: **no concurrency split at all** — keep the single shared semaphore
  and worker pool exactly as today, with informal round-robin fairness
  across every market/version, same as domestic/intl-v1/external API already
  share. The two sandbox *containers* still exist (for tooling/model
  differentiation), they just aren't a concurrency mechanism.
- **Country guardrail is intl v2's structural analogue of domestic v2's
  metadata guardrail**, but on a different signal: `MovieMasterIntl` has no
  `director`/`cast`/`synopsis` columns at all, so the domestic rule (both
  empty ⇒ disregard) is literally inapplicable. The one deterministic signal
  international actually has is country consistency, so that's what the new
  `intl_guardrail_v2.py` checks instead.
- **Genre-consistency guardrail: explicitly out of scope.** Genre matching
  is a semantic judgment call with no clean, data-verified rule like
  domestic's Sports/Concert-Special-Events exemption — it stays prompt-only.
- **Rerank ships as-is**: always-on, same model as the main pass, no
  confidence-based gating — matching the original design, but now with its
  Bedrock usage actually logged (a real cost blind spot in the original).
- **Accept the brief post-deploy degraded window**: until
  `rebuild-semantic-index-intl --backfill-country` runs, intl v2's Vespa
  country filter matches nothing on existing rows. Chosen over gating the
  feature behind an extra flag.
- **Branch off `stage`** (which was fully synced with `main` at the time),
  not `main` directly, per the user's explicit instruction — and commit
  after every phase rather than one giant commit at the end.

## What was built (9 commits on `feat/intl-title-match-v2`, phase by phase)

1. **Settings** — `AGENTIC_INTL_V2_*` / `CLAUDE_SANDBOX_URL_INTL` /
   `AGENTIC_INTL_V1_USE_INTL_SANDBOX`, all inert until later commits.
2. **Vespa country-aware matching restored** — `movie_master_intl.sd` gains
   a `country` field; `semantic_index.py`/`semantic_tasks.py` gain the
   feed-side plumbing plus `force`/`force_deploy` flags; `cli.py` gains the
   one-time `rebuild-semantic-index-intl` backfill command. Guarded on
   `schema == _VESPA_SCHEMA_INTL` throughout — domestic untouched.
3. **A second `claude-sandbox` container** (`claude-sandbox-intl`) — same
   image, differentiated purely by env (`SANDBOX_PROFILE`/`MCP_CONFIG_PATH`/
   `SETTINGS_PATH`/`CLAUDE_DEFAULT_MODEL`/`EXTRA_ALLOWED_TOOLS`/Serper locale
   bias). `runner.py` gains a keyword-only `sandbox_url` param (default
   `None` = today's behavior) resolved via new `sandbox_target.py`, plus a
   guard rejecting `variant="v2"` with `market="international"` through the
   shared function (that combination must only ever reach the standalone
   intl v2 orchestrator).
4. **International v2 module** — six new files:
   `candidates_intl_v2.py` (forked DB/Vespa candidate fetch, not shared),
   `prompt_builder_intl_v2.py` (full standalone prompt restoring the
   anniversary/country-hygiene rules, plus a genre/genre2 weighing step),
   `intl_guardrail_v2.py` (deterministic country-consistency check),
   `rerank_intl_v2.py` (the restored verification pass, intl-v2-only trigger,
   now with usage logging), `post_lookup_intl_v2.py` (intl's confidence fix),
   `runner_intl_v2.py` (the standalone orchestrator tying it together, reusing
   `runner.py`'s transport/throttle plumbing directly but never calling
   `run_agentic_match`). Also adds `rerelease_lookup_title` to
   `TitleMatchResult`/`result_parser.py` — the one deliberate shared-file
   touch, a no-op for v1/domestic-v2 output.
5. **Domestic v2's own post-lookup fix** — `post_lookup_v2.py` + a 4-line
   `runner.py` diff (one `if is_v2:` branch). Domestic v1 unaffected.
6. **International v2 routes** — `/api/v2/intl-movie-title-match/{single,
   batch,batch/{job_id},batch/{job_id}/download}`, separate prefix (not a
   `market` field on the domestic v2 router), `country` required.
7. **Batch pipeline wiring** — `MovieTitleIntlBatchJob` gains
   `pipeline_variant` (migration `a1b2c3d4e5f7`, reusing the table rather
   than a third one, mirroring domestic v2's identical rationale);
   `agentic_intl_match_task.py` gains the same two seams
   (`agentic_intl_match_task.py`'s `_variant_of`, the row task's `variant`
   kwarg, `enqueue_next_window`'s conditional publish kwarg) domestic
   already has.
8. **Tests** — one consolidated file (`test_intl_v2.py`, 43 tests) rather
   than several, per earlier feedback to keep test-file count down.
9. **Docs** — `docs/CLAUDE.md`, `movie-title.md`, this file.

## Verification

- Full suite after every phase: 699 passed, 3 skipped, plus the same three
  pre-existing environment-caused flakes already documented yesterday
  (shared Redis queue polluted by the live stack's own traffic; Vespa index
  state from real synced data) — reconfirmed unrelated, not introduced by
  this branch.
- Both migrations applied to the local dev Postgres.
- All 8 new API routes (4 domestic v2 + 4 intl v2) confirmed present in
  `/openapi.json`.

## Open items / follow-ups (not done this session)

- No genre-consistency deterministic guardrail for international v2 —
  prompt-only, pending a data-verification pass over production
  `MovieMasterIntl` genre values if this turns out to matter in practice.
- `rebuild-semantic-index-intl --force-deploy --backfill-country` has not
  yet been run against production data — required before intl v2's country
  filter actually does anything on existing rows.
- No frontend work — intl v2 is API-only, same as domestic v2.
- International v1, the external API's international path, and intl v1
  batch all remain exactly as they were — including the stale-confidence
  bug — by deliberate choice.
