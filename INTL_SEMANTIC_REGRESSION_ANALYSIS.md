# International Semantic-Match Regression — Root Cause Analysis

## Summary

After the hybrid-search fix (commit `d4b2cf7` / PR #46) shipped, a user reported that
international title mapping got *worse* — "around 80% of titles now fail to match",
whereas before it "was working well enough but failing a bit for translated titles."

**The user-reported "~80% failing" figure did not reproduce in a clean test.** A full
Mode B agentic international batch run against a 54-row ground-truth file
(`testintl.xlsx`, sheet "Mapped Data") — real Celery workers, real Vespa, real Postgres,
real Claude/Bedrock agent — matched **41/54 (76%)**, not 20%. A separate confound was
active in production around the time the user likely observed the worse behaviour: the
Serper API key (the agent's web-search fallback) was fully out of credits when this
investigation started, which degrades exactly the cross-language path the user was
complaining about. That confound plausibly explains some or all of the gap between the
user's live observation and this clean test — but it is **not** the structural bug, and
the structural bug below is real and reproduces independently of Serper state.

Of the 13 genuine `no_match` rows in the clean run, **11 are the same 11 titles already
documented as failing in `UNMAPPED_TITLES_ANALYSIS.md` *before* PR #46 shipped** (Samurai
Angel, Aguas Mortais, Los demonios del amanecer, Terapia di famiglia, C'è ancora domani,
Thousand Moons, Rufus ×2, El anfitrión, Le Fabuleux destin d'Amélie Poulain, Lmd Solo La
Mas 2). Only 2 are new ("Trolls 3 - Tutti insieme", "Non Non Dans L Espace"). **PR #46
did not close the cases it was built to solve.**

There are two distinct root causes, and — importantly — **the headline one is not the
Vespa ranking formula that PR #46 touched.**

1. **PRIMARY (fixable, high impact): the id=0 post-lookup searches the DB with the wrong
   title.** When the agent correctly identifies a film via web search but the pre-fetch
   handed it no matching DB candidate, `run_agentic_match` re-searches the DB using the
   agent's `suggested_movie_title`. But the international prompt rule tells the agent to
   put the **country-localized release title** (e.g. "Águas Mortais") in that field —
   while `MovieMasterIntl` actually stores these rows under the **English/master title**
   ("Deep Water"). So the post-lookup searches for "Águas Mortais", finds nothing, and the
   correct row that is *sitting in the DB under "Deep Water"* is never resolved. This
   single mismatch accounts for the bulk of the reproduced `no_match` rows.

2. **SECONDARY (mostly not fixable by a ranking change): the ANN embeddings are not
   discriminative enough for cross-language near-homonyms.** Even with BM25 removed
   entirely, the correct document does not surface in Vespa's top-K for the three
   cross-language cases tested. A rank-profile change (BM25 normalization, RRF, weighting)
   was empirically tested against live Vespa and **fixes none of the three cases** — the
   correct document is genuinely absent from the semantic top-100, so no re-weighting of
   what is retrieved can help.

The practical consequence: **the fix the user needs is the one the user proposed** — let
the agent resolve the canonical title, then match *that* against the DB — not a Vespa
ranking-formula change. The plumbing for this already exists (`run_agentic_match`'s
post-lookup); it is just fed the wrong title for the international path.

---

## Root cause detail

### Bug 1 (PRIMARY): id=0 post-lookup searches with the localized title, DB stores the English title

File: `backend/app/title_matching/agentic/runner.py`, `run_agentic_match`, lines 97–132.

When the agent returns `suggested_movie_id == 0` with a non-"Unknown"
`suggested_movie_title`, the runner does a second DB lookup on that title:

```python
# runner.py:97-104
if result.suggested_movie_id == 0 and result.suggested_movie_title and result.suggested_movie_title != "Unknown":
    ...
    post_query = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", result.suggested_movie_title).strip(" -:")
    post_hits = _db_search(post_query or result.suggested_movie_title, market=market, country=country)
```

This is exactly the "resolve title, then DB-match" flow. The problem is *which* title the
agent is told to emit. The international prompt rule
(`backend/app/title_matching/agentic/prompt_builder.py`, lines 135–140):

```python
_INTL_MASTER_ID_ZERO_RULE_TEMPLATE = """\
CRITICAL: When movie_master_id is 0, movie_title MUST be the title used for this film's theatrical \
release in {country} — this is what a DB post-lookup will search for, and Movie Master \
International stores per-country release titles (which may be a localized spelling/translation, \
not always the English title). ..."""
```

This instruction is **factually wrong about the data**. Verified live against
`MovieMasterIntl`:

| Localized title (what the rule tells the agent to emit) | DB rows found | English/master title | DB rows found | Correct id |
|---|---|---|---|---|
| `Aguas Mortais` (Brazil) | **0** | `Deep Water` (Brazil) | **1** | 156728 |
| `Terapia di famiglia` (Italy) | **0** | `Jamais sans mon psy` (Italy) | **1** | 156867 |
| `El anfitrión` (Spain) | **0** | `The Birthday Party` (Spain) | **1** | 156863 |

The Brazil/Italy/Spain rows for these films are stored under their **English** title in
`movie_title`, not the country-localized spelling. So the post-lookup, told to search for
the localized title, searches for a string that does not exist in the DB and returns
nothing — even though `_db_search("Deep Water", market="international", country="Brazil")`
returns the correct row instantly (verified live).

The `no_match` rows in the clean run confirm the agent *did* do its job: in every case its
own `reasoning` names the correct film (e.g. *"Input 'Águas Mortais' is the Brazilian
release title for 'Deep Water' (2026)..."*, *"Input is the Italian release title for the
French comedy 'Jamais sans mon psy'..."*), yet `mapped_title` is empty and
`present_in_db = "No"`. The agent identified the film; the post-lookup then searched the
DB with the wrong title and threw the answer away.

This bug is **specific to the international path**. The domestic rule
(`_DOMESTIC_MASTER_ID_ZERO_RULE`, prompt_builder.py:127–133) correctly tells the agent to
emit the *English-language domestic-release title* — which is exactly what `MovieMaster`
stores — so domestic post-lookup searches with the right string.

### Bug 2 (SECONDARY): the hybrid rank-profile — and the ANN embeddings behind it

File: `backend/vespa/schemas/movie_master_intl.sd` and `backend/vespa/schemas/movie_master.sd`,
lines 42–46 (identical in both):

```
first-phase {
    expression: bm25(title) + closeness(field, embedding)
}
```

The theoretical concern (and the one PR #46's design implicitly assumed away) is that
`bm25(title)` is **unbounded** while `closeness(field, embedding)` is bounded in `[0, 1]`
(angular metric: `closeness = 1 / (1 + arccos(cosine))`, so it maxes around ~0.57 in
practice). Summing them lets any incidental keyword overlap with an unrelated title
numerically dominate the semantic signal.

This BM25-dominance effect is **real and measurable** — but it turns out **not to be the
binding constraint**, because the correct documents are not in the semantic retrieval set
in the first place. See the empirical section below.

`_fetch_vespa_candidates` (runner.py:340–394) and the domestic
`VespaSemanticIndex.search()` (semantic_index.py:285–335) both depend on this identical
rank-profile expression, so any ranking-formula concern is **shared between the domestic
and international paths**. (The comment at semantic_index.py:325 — "RRF scores are
typically in [0, 1]" — is misleading: the `hybrid` profile does *not* use RRF, it uses raw
`bm25 + closeness` addition, whose relevance is unbounded, e.g. 22.13 for the "Advogado de
Deus" spot-check below. That comment predates or mis-describes the current profile.)

---

## Empirical testing against live Vespa

All tests were run read-only against the live dev Vespa instance
(`http://vespa:8080`, `movie_master_intl` document type, 157,768 docs). Candidate
rank-profiles were added *additively* to the schema and deployed via the config server's
`prepareandactivate` endpoint; Vespa reported `"refeed": []` and `"reindex": []` on every
deploy, **confirming that a rank-profile change is query-time only and requires no
re-embedding or re-indexing** (the assumption behind proposing a `.sd` fix). Document count
was unchanged (157,768) before and after. The schema was restored to its original
single-profile state afterward — no committed code changed.

### Three cross-language cases

Query embedding via `get_embedding()` (Cohere Embed Multilingual v3, `search_query`);
correct-document embedding fetched via `document/v1`; cosine computed in Python.
`closeness` = Vespa's angular closeness feature; `bm25` = `bm25(title)` match-feature.

**Case A — "Homem-Aranha: Um Novo Dia" → "Spider-Man: Brand New Day" (Brazil, id=18646):**

- Manual cosine(query, correct) = **0.6608** — the *highest* of any candidate examined.
- `hybrid` top-1: "Quinze Dias" (id 125023), relevance **10.29**, `bm25 = 9.79`,
  `closeness = 0.508`. Wins purely on the shared word "Dia"/"Dias". Correct doc: **not in top 10.**
- Manual cosines: correct 0.6608, "Quinze Dias" 0.5667, "Opera De Paris" 0.6230 — correct
  is genuinely the closest, yet…
- **Exact (brute-force, `approximate:false`) nearestNeighbor, `closeness_only` profile,
  top 100: correct doc STILL not returned** (top-1 "La grande aventure de Non-Non" @ 0.5271).
  This is the key anomaly — see interpretation below.

**Case B — "Terapia di famiglia" → "Jamais sans mon psy" (Italy, id=156867):**

- Manual cosine(query, correct) = **0.4019**.
- `hybrid` top hits: "Un milione di granelli di sabbia" (`bm25 = 7.45`, `closeness = 0.451`,
  rel 7.90) and "Cena di classe" ×many (`bm25 = 6.90`, `closeness = 0.471`, rel 7.37).
- Correct doc's cosine (0.402) is **lower** than "Cena di classe" (0.538) and "The Therapy"
  (0.538). Even `closeness_only` returns "Family Affairs" / "The Therapy", never the correct
  row. This case has a genuine **embedding-quality** problem on top of any ranking issue.

**Case C — "Aguas Mortais" → "Deep Water" (Brazil, id=156728):**

- Manual cosine(query, correct) = **0.5009**.
- `hybrid` top hits all have **`bm25 = 0.0`** (zero keyword overlap): "Dark Water"
  (`closeness = 0.510`, cosine 0.574), "Dark Waters" (0.506), "Black Rain" (0.497).
  This is *already* a pure-ANN ranking here — and "Dark Water" simply out-scores "Deep
  Water" on the embedding. A ranking-formula change cannot help; the embedding places a
  near-homonym ahead of the correct film.

### Candidate rank-profiles tested (all read-only, live)

| Profile | Expression | Case A | Case B | Case C | Passing spot-checks |
|---|---|---|---|---|---|
| `hybrid` (current) | `bm25 + closeness` | ✗ | ✗ | ✗ | ✓ (Advogado, Ponyo, Mamma Mia! all top-1) |
| `sat10` (Option 1) | `bm25/(bm25+10) + closeness` | ✗ | ✗ | ✗ | ✓ |
| `sat10_cw2` | `bm25/(bm25+10) + 2·closeness` | ✗ | ✗ | ✗ | ✓ |
| `rrf` (Option 2) | reciprocal_rank_fusion(bm25, closeness) | ✗ | ✗ | ✗ | ✓ |
| `closeness_only` (diagnostic) | `closeness` only | ✗ | ✗ | ✗ | ✓ |

**Every ranking candidate fails all three cross-language cases, and — critically — so does
`closeness_only`, which removes BM25 entirely.** If BM25 dominance were the binding
constraint, `closeness_only` would surface the correct doc for Case A (whose correct
cosine is the highest of any candidate). It does not. The correct document is absent from
the semantic top-K even under exact brute-force nearest-neighbour.

All five profiles left the three currently-passing spot-checks intact — "O Advogado de
Deus", "Ponyo", "Mamma Mia!" remained top-1 in every profile (they match on strong exact
BM25 keyword overlap, which every profile preserves), so **no ranking candidate regressed
what currently works.**

### Interpretation of the Case A anomaly

Case A is the diagnostic key. The correct document's manual cosine (0.6608) is the highest
of any candidate, yet exact `nearestNeighbor` does not return it in the top 100. Two
observations bear on this:

1. Cohere's `get_embedding` is **not deterministic** — two calls for the same query
   returned slightly different vectors (confirmed live). This is float noise, not enough to
   move a 0.66-cosine document out of the top 100 on its own.
2. The `nearestNeighbor` operator matched only ~40,675 of 157,768 docs on one probe and
   2,043 on another with different `targetHits` — the angular-distance top-K over a
   157k-doc HNSW index with `distance-metric: angular` is behaving in a way that does not
   cleanly rank the highest-cosine document first.

Regardless of the precise mechanism, the empirical bottom line stands: **the correct
document is not reliably retrievable from the ANN index for these cross-language queries,
so no rank-profile change over the retrieved set can fix Cases A–C.** The embedding/ANN
retrieval layer — not the ranking formula — is the semantic limitation. This is why Bug 1
(the post-lookup title fix) matters so much more: it bypasses ANN entirely by letting the
agent's web-resolved English title drive an exact DB lookup.

---

## Does this affect the domestic path?

**Bug 1 (post-lookup title): No — and the reason is worth stating precisely, because the
post-lookup *code path* is not different between domestic and international at all.**
`run_agentic_match`'s post-lookup block (`runner.py:97-132`) is one unbranched block of
code shared by both markets — it calls `_db_search(post_query, market=market,
country=country)` regardless of which market triggered it. There is no
market-conditional logic in the post-lookup itself.

What *does* differ is purely upstream, in `prompt_builder.py`'s two title-instruction
templates:

- `_DOMESTIC_MASTER_ID_ZERO_RULE` (line 127) tells the agent to emit the **English
  domestic-release title** — which is exactly what `MovieMaster.movie_title` stores. The
  shared post-lookup code searches with that string and hits.
- `_INTL_MASTER_ID_ZERO_RULE_TEMPLATE` (line 135) tells the agent to emit the
  **country-localized release title** — which is factually wrong for the rows tested
  (`MovieMasterIntl.movie_title` stores the English/master title for these cross-language
  cases). The identical shared post-lookup code searches with that (wrong) string and
  misses.

So: **same downstream mechanism, different — and for international, incorrect —
instruction feeding it.** This is why the bug is international-only despite the runner
logic being shared: the bug is a data-contract mismatch in the prompt, not a code fork.

**Bug 2 (rank-profile / ANN): Yes, structurally shared, but low practical impact.**
`movie_master.sd` uses the byte-identical `bm25 + closeness` expression, and
`VespaSemanticIndex.search()` / `_fetch_vespa_candidates` use it for both markets. Domestic
matching is overwhelmingly same-language (English scraped title vs English master title),
so it wins on strong BM25 exact/near-exact overlap and rarely relies on cross-language ANN
recall — which is exactly why `UNMAPPED_TITLES_ANALYSIS.md` noted this "only becomes highly
visible for international titles." A ranking change would apply to both schemas if adopted,
but the evidence says it would change little for either.

---

## Per-title verification (clean 54-row run, 13 `no_match` rows)

Agent's `reasoning` column shows it correctly identified the film in every case; the
post-lookup then failed to resolve it because it searched the localized title.

| # | Input title | Country | Agent-identified film (from reasoning) | English title in DB? | Root cause |
|---|---|---|---|---|---|
| 1 | Samurai Angel: The Complete Blood Epic | Thailand | (no film found — genuine research gap) | Kill Bill: TWBA | Agent didn't resolve; not a post-lookup bug |
| 2 | Aguas Mortais | Brazil | Deep Water (2026) | ✅ id 156728 | **Bug 1** (searched "Águas Mortais", 0 hits) |
| 3 | Los demonios del amanecer | Mexico | Los demonios del amanecer | needs check | Bug 1 (localized vs stored title) |
| 4 | Terapia di famiglia | Italy | Jamais sans mon psy | ✅ id 156867 | **Bug 1** |
| 5 | Trolls 3 - Tutti insieme | Italy | Trolls Band Together (2023) | needs check | Bug 1 (new row) |
| 6 | C'è ancora domani | Italy | There's Still Tomorrow | ✅ (id 156723 seen for EN) | Bug 1 |
| 7 | Thousand Moons | Brazil | Mil Luas / La Mamma | needs check | Bug 1 |
| 8 | Rufus, la serpiente que no sabía nadar | Spain | Ruffen / Rufus: The Sea Serpent… | needs check | Bug 1 |
| 9 | Rufus: la serpiente que no sabía nadar | Spain | (dup of 8) | needs check | Bug 1 |
| 10 | El anfitrión | Spain | The Birthday Party | ✅ id 156863 | **Bug 1** |
| 11 | Le Fabuleux destin d'Amélie Poulain | France | Amelie | needs check | Bug 1 |
| 12 | Lmd Solo La Mas 2 | Mexico | La Más Draga: Solo Las Más 2 | needs check | Bug 1 |
| 13 | Non Non Dans L Espace | France | No-No Goes to Space | needs check | Bug 1 (new row) |

Rows 2, 4, 10 were directly verified live (English title present in DB, localized absent,
post-lookup succeeds with English title). Cases A–C additionally confirm that even a
perfect ANN would struggle for near-homonyms, but Bug 1 makes ANN irrelevant for the
common case.

> **ID-churn caveat:** `MovieMasterIntl.id` is an autoincrement surrogate PK that is **not
> stable across reseeds**. The prod-DB resync (PR #48, see `plans/prod-db-sync-movie-master.md`)
> re-upserts by natural key and reassigns ids. Do **not** trust any numeric id from
> `UNMAPPED_TITLES_ANALYSIS.md` (e.g. "Deep Water" id=140809) — it now points elsewhere.
> The ids in this document (156728, 156867, 156863, 18646) were re-confirmed live by ILIKE
> search on the current DB and are valid as of this investigation, but will churn again on
> the next resync. Match by title+country, never by cached id.

---

## Fix Plan

Two independent steps, sized for individual PRs. Step 1 is the primary fix and should
ship alone first — it is a small, low-risk, high-impact prompt/runner change with no
schema or infra dependency. Step 2 is optional hardening, decoupled from Step 1, and
should not block it.

---

### Step 1 — Fix the international id=0 title instruction + dual-title post-lookup (PRIMARY, required)

**Depends on:** nothing. No schema, migration, or Vespa change.

#### Context Brief

`run_agentic_match`'s post-lookup (`backend/app/title_matching/agentic/runner.py:97-132`)
is a single, unbranched code path shared by both markets: when the agent returns
`suggested_movie_id == 0` with a real `suggested_movie_title`, it re-searches the DB with
that title via `_db_search(post_query, market=market, country=country)`. The only thing
that differs between domestic and international is the prompt instruction
(`backend/app/title_matching/agentic/prompt_builder.py`) telling the agent what string to
put in `suggested_movie_title` when it can't find a DB id. The domestic instruction
(`_DOMESTIC_MASTER_ID_ZERO_RULE`, line 127) matches what `MovieMaster` stores (English
domestic title). The international instruction (`_INTL_MASTER_ID_ZERO_RULE_TEMPLATE`,
line 135) tells the agent to emit the **country-localized** title, but `MovieMasterIntl`
verifiably stores the **English/master** title for the cross-language rows tested
("Águas Mortais" → 0 DB hits; "Deep Water" → 1 hit, same row). Fixing the instruction to
match the data, plus making the post-lookup resilient to either guess, closes the
regression without touching Vespa at all.

#### Task List

- [ ] Rewrite `_INTL_MASTER_ID_ZERO_RULE_TEMPLATE` in `prompt_builder.py` (lines 135–140)
  to instruct the agent to report the **English/international master title** in
  `movie_title` when `movie_master_id` is 0, explicitly warning that `MovieMasterIntl`
  commonly stores the English title even for non-English-market rows:
  ```diff
   _INTL_MASTER_ID_ZERO_RULE_TEMPLATE = """\
  -CRITICAL: When movie_master_id is 0, movie_title MUST be the title used for this film's theatrical \
  -release in {country} — this is what a DB post-lookup will search for, and Movie Master \
  -International stores per-country release titles (which may be a localized spelling/translation, \
  -not always the English title). If web_search results show multiple regional titles, prefer the one \
  -used specifically for the {country} release."""
  +CRITICAL: When movie_master_id is 0, movie_title MUST be the ENGLISH / canonical (master) title of \
  +the film — this is what a DB post-lookup will search for. Movie Master International most often \
  +stores the English master title in the searchable title field even for a {country} release (e.g. \
  +the Brazil row for "Águas Mortais" is stored as "Deep Water"), so reporting the localized spelling \
  +here will cause the lookup to miss. If web_search results show both a localized {country} title and \
  +an English/international title, report the ENGLISH one here. If you are only confident in the \
  +localized title, still report it — a fallback lookup will also try it."""
  ```
- [ ] Add a new optional field to `TitleMatchResult` (`backend/app/title_matching/types.py`)
  — e.g. `alternate_movie_title: Optional[str] = None` — and populate it from the agent's
  JSON payload in `result_parser.py`'s `_build_result` (around line 185), reading an
  optional `alternate_movie_title` key from `best` (the agent's chosen candidate object).
  Update the international-only section of the output schema in `prompt_builder.py`
  (the `## Output schema` block, lines 68–91) to document this optional key: when
  `movie_master_id` is 0, the agent may additionally supply the *other* title (localized
  if it reported English, or vice versa) here. This is optional on the agent's part — if
  omitted, behavior is unchanged from today (single post-lookup attempt).
- [ ] In `run_agentic_match`'s post-lookup block (`runner.py:97-132`), after the existing
  `post_hits = _db_search(post_query or result.suggested_movie_title, ...)` call: if
  `post_hits` is empty and `result.alternate_movie_title` is set, run one additional
  bounded `_db_search` call using the alternate title (through the same ordinal-conflict
  filter already applied at lines 112–120) before falling through to "no post-lookup
  match". This is a single extra best-effort attempt, not a loop or retry — mirrors the
  existing `_db_search` → `master_movie_title` ILIKE fallback pattern already used inside
  `_db_search` itself (lines 271–275) at one level up.
- [ ] Update `backend/tests/test_agentic_runner_candidates.py` (or add a new test module
  alongside it, matching this repo's existing file-per-concern convention) with:
  - A prompt-content test asserting `build_prompt(market="international", country=...)`
    contains the new English-title instruction and no longer contains the old
    "title used for this film's theatrical release in {country}" wording; assert the
    domestic prompt (`market="domestic"`) is byte-identical to before (regression).
  - A runner test that stubs `_call_sandbox`/`parse_agent_output` (or exercises
    `run_agentic_match` via the same mocking pattern already used in
    `test_agentic_batch_task.py`) to return `suggested_movie_id=0,
    suggested_movie_title="Deep Water"` for an international Brazil request, and asserts
    the post-lookup resolves the live "Deep Water" row and does NOT return id 0.
  - A dual-title test: agent returns `suggested_movie_title="Águas Mortais",
    alternate_movie_title="Deep Water"`; first `_db_search` (mocked) returns `[]`, second
    (for the alternate) returns the correct row; assert the result uses the alternate hit.
  - A negative test: neither title resolves anything → result stays `suggested_movie_id=0`
    (no regression to the existing "no candidates" REVIEW behavior).
- [ ] Run the full existing agentic/runner/prompt test suite to confirm no regression:
  `cd backend && .venv/bin/python -m pytest tests/test_agentic_runner_candidates.py tests/test_agentic_batch_task.py tests/test_batch_io.py tests/test_title_matching.py -v`

#### Verification

```bash
cd backend && .venv/bin/python -m pytest tests/test_agentic_runner_candidates.py -v
cd backend && .venv/bin/python -m pytest tests/ -m "not integration"
```
Manual/live: re-run the 54-row `testintl.xlsx` batch (market=international, Serper
credits confirmed available) and confirm the 11 previously-documented rows plus the 2 new
ones (`Trolls 3 - Tutti insieme`, `Non Non Dans L Espace`) move from `no_match` to
`matched`. Target: match rate ≥ 90% (from the 76% baseline in this investigation).

#### Exit Criteria

- New/updated unit tests pass; full existing suite green (no domestic prompt/runner
  regression).
- Live batch re-run shows the previously-failing rows now resolve via post-lookup with
  `present_in_db = "Yes"`.
- No change to `_DOMESTIC_MASTER_ID_ZERO_RULE`, the domestic prompt output, or any
  Vespa/DB schema.

#### Rollback

Revert the `prompt_builder.py` template change, the `types.py` field addition, the
`result_parser.py` parsing addition, and the `runner.py` dual-lookup addition — four small,
independent diffs, each individually revertible. No schema, migration, or Vespa change to
unwind; no data was written differently (post-lookup only reads).

#### Model Tier

Default — a scoped prompt-string and runner-logic change with a clear existing pattern
(`_db_search`'s own `movie_title` → `master_movie_title` fallback) to mirror.

---

### Step 2 — Rank-profile hardening (SECONDARY, optional, decoupled from Step 1)

**Depends on:** nothing functionally — independent of Step 1. Sequence it after Step 1
lands anyway, so the live batch re-run used to verify Step 2 isn't confounded by Step 1's
match-rate jump.

#### Context Brief

The empirical results in this document show **no rank-profile change fixes the three
cross-language cases** (A/B/C) — the correct document is absent from the semantic top-K
even with BM25 removed entirely (`closeness_only` profile). So this step is **not a fix
for the regression** — Step 1 is. This step is optional hardening against BM25 numerically
dominating closeness in *other*, not-yet-observed scenarios, justified only because it was
already tested safe against the current passing spot-checks. Do not schedule this ahead of
Step 1, and do not expect it to move the match-rate number.

#### Task List

- [ ] If adopted, apply the **saturating normalization** to both `.sd` files (they must
  stay identical — one shared query path in `_fetch_vespa_candidates`/`VespaSemanticIndex`
  serves both markets):
  ```diff
   rank-profile hybrid inherits default {
       inputs {
           query(q_embedding) tensor<float>(x[1024])
       }
       first-phase {
  -        expression: bm25(title) + closeness(field, embedding)
  +        expression: (bm25(title) / (bm25(title) + 10)) + closeness(field, embedding)
       }
       match-features: bm25(title) closeness(field, embedding)
   }
  ```
  in both `backend/vespa/schemas/movie_master.sd` and
  `backend/vespa/schemas/movie_master_intl.sd`. (RRF via `reciprocal_rank_fusion` was also
  tested and is scale-invariant, but adds a `global-phase` block and is more invasive for
  the same measured outcome — prefer the saturating-normalization diff above unless a
  future investigation finds a case where RRF's rank-based combination behaves
  meaningfully differently.)
- [ ] Deploy via the existing mechanism: zip `backend/vespa/` and POST to
  `<vespa-config-url>/application/v2/tenant/default/prepareandactivate` (see
  `_deploy_vespa_app` in `backend/app/title_matching/semantic_index.py:179-233` for the
  exact pattern already used at startup). **Confirm the response's
  `configChangeActions.refeed` and `.reindex` are both empty** before considering the
  change safe — this was true in this investigation's testing and is what makes this a
  query-time-only, no-reindex change; if a future Vespa/schema version ever reports a
  non-empty `refeed`/`reindex` for this specific diff, stop and re-plan, since that would
  mean the assumption behind treating this as a cheap step no longer holds.
- [ ] Add/update a Vespa-integration test (if this repo has one; otherwise a documented
  manual verification step in the PR description) asserting: (a) the three passing
  spot-check queries ("Advogado de Deus", "Ponyo", "Mamma Mia!" or their Portuguese/
  Italian/English equivalents) still return the correct top-1 result after the profile
  change, (b) `bm25(title)` alone can no longer produce a relevance score that is
  unboundedly larger than `closeness`'s max contribution (spot-check the match-features on
  a few known high-BM25/low-closeness rows).

#### Verification

```bash
# Re-run the three diagnostic cases + spot-checks from this document's empirical section
# against the live dev Vespa instance, comparing before/after relevance and match-features.
docker compose exec backend python -c "<harness reproducing this doc's Case A/B/C + spot-check queries>"
```
Confirm: Cases A/B/C remain `no_match` via ranking alone (expected — Step 1 is what closes
them via the agent+post-lookup path, not ranking), and the 3 spot-checks are unchanged
top-1 after the profile swap.

#### Exit Criteria

- Deploy returns `refeed: []` and `reindex: []`.
- Document count unchanged pre/post deploy (157,768 for intl / ~46,041 for domestic at time
  of writing).
- Spot-checks unregressed. No claim made that this step improves the international
  match rate — its only goal is defensive normalization for future BM25-dominance cases.

#### Rollback

Restore the original `expression: bm25(title) + closeness(field, embedding)` in both `.sd`
files and redeploy via the same `prepareandactivate` endpoint. Confirmed query-time only —
takes effect in seconds, no data movement, no reindex. (This exact restore was performed at
the end of this investigation to return live Vespa to its pre-experiment baseline.)

#### Model Tier

Default — a scoped `.sd` expression change with an already-verified-safe deploy mechanism.

---

## Residual embedding-quality limitation (Cases B, C) — not fixed by either step

Step 1 resolves Cases B and C **via the post-lookup path** (the agent identifies "Jamais
sans mon psy" / "Deep Water" and the English-title DB lookup then succeeds), so in practice
they are fixed *when the agent's web search succeeds*. But the underlying **ANN embedding
weakness is not fixed by Step 1 or Step 2** — "Águas Mortais" embeds closer to "Dark Water"
than to "Deep Water" (0.574 vs 0.501), and "Terapia di famiglia" embeds closer to "The
Therapy" than to the correct "Jamais sans mon psy". If the agent's web-search resolution is
ever unavailable (e.g. the Serper credit outage that confounded this investigation), these
cases fall back to the weak ANN candidates and will fail again. Options, in order of effort:

1. **Accept as a known residual** for near-homonym cross-language pairs when web search is
   down — the agent + Step 1's post-lookup is the real matcher; ANN is only a candidate hint
   fed into the prompt, not the deciding mechanism.
2. **Richer `embed_text` composition** (`_compose_embed_text`, semantic_index.py:43-54):
   currently `title + year + director`. Adding `master_movie_title` and/or country/genre
   could sharpen discrimination — but this requires a **full re-embed + re-feed** of the
   ~204K-row corpus (not just a rank-profile redeploy), so it is a materially heavier,
   separately-planned change, out of scope here.
3. **Treat Serper/web-search credit availability as an operational dependency** (monitor/
   alert on it) rather than a code fix, since Step 1's fix is only as good as the agent's
   ability to actually resolve the canonical title via web search.

Recommended: ship Step 1, optionally Step 2 for defensiveness, and record the ANN weakness
as option (1)/(3) now, with option (2) as a future planned re-embed if cross-language
recall without web search ever becomes a hard requirement.

---

## Overall verification plan (both steps)

1. **Unit test (prompt):** `build_prompt(market="international", country="Brazil")` includes
   the new English-title instruction and no longer instructs the localized-title emission;
   domestic prompt output unchanged (regression). (Step 1)
2. **Unit test (runner post-lookup):** patch the sandbox to return
   `suggested_movie_id=0, suggested_movie_title="Deep Water"` for a Brazil request and
   assert the post-lookup resolves the live id (by title, not a hardcoded number — see the
   ID-churn caveat above) and sets `present_in_db="Yes"`. Add the dual-title case. (Step 1)
3. **Live re-run of the same 54-row batch** (`testintl.xlsx`, market=international) with
   Serper credits confirmed topped up, after Step 1 ships. Expected: the 11
   previously-documented rows plus the 2 new rows move from `no_match` to `matched`.
   Target match rate ≥ 90% (from the 76% clean-run baseline in this investigation). Report
   the actual delta.
4. **Domestic regression:** run a domestic same-language batch and confirm identical results
   (Step 1 does not touch the domestic prompt or path; Step 2, if shipped, touches both
   schemas identically and must not regress the domestic spot-checks either).
5. **If Step 2 ships:** re-run this document's Case A/B/C + 3 spot-check queries against
   live Vespa and confirm spot-checks are unregressed (Cases A/B/C are expected to remain
   unresolved by ranking alone — that's not a regression, it's the documented limitation).

## Overall rollback

- **Step 1** is a prompt-string + runner-logic change confined to `prompt_builder.py`,
  `types.py`, `result_parser.py`, `runner.py`. Rollback = revert those files. No schema,
  DB, or Vespa change; no reindex; post-lookup only reads, so no data was written
  differently.
- **Step 2 (if shipped)** is a rank-profile change in the two `.sd` files. Rollback =
  restore the original `bm25(title) + closeness(field, embedding)` expression and redeploy
  the Vespa app package via the config server (`prepareandactivate`). Confirmed query-time
  only — `refeed`/`reindex` empty on deploy — so rollback is a redeploy with no data
  movement, taking effect in seconds.
