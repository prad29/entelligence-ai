# Unmapped International Titles — Root Cause Analysis

## Summary

All 12 titles in `Unmapped Titles - Sheet1.csv` have a real, correct row in `MovieMasterIntl` under their English/master title. **None of these are missing data.** Every failure is a candidate-retrieval problem: the pre-fetch step that runs before the LLM never surfaced the correct row as a candidate, so the agent (correctly, per its instructions) refused to force a match and returned `movie_master_id: 0`.

There is exactly **one root cause** behind all 12 failures, plus one contributing factor:

1. **`_fetch_vespa_candidates()` never queries by semantic embedding — it is BM25-keyword-search only.** This is the primary cause and explains all 12 failures.
2. **pg_trgm trigram fallback can't help either**, because these are true cross-language translations (not spelling/accent variants), so their trigram similarity scores are far below the matching threshold.

## Root cause detail

### Bug: `_fetch_vespa_candidates` drops the embedding/ANN clause entirely

File: `backend/app/title_matching/agentic/runner.py`, function `_fetch_vespa_candidates` (~line 327).

Current YQL:
```python
"yql": f"select * from sources {schema} where userQuery()",
```

This is pure BM25 keyword matching against the `title` field — `userQuery()` alone, with no `nearestNeighbor(embedding, q_embedding)` clause and no `input.query(q_embedding)` vector passed in the request body. Compare this to the *other* Vespa search path in the codebase, `VespaSemanticIndex.search()` in `backend/app/title_matching/semantic_index.py` (~line 302), which correctly does:
```python
"yql": f"select {id_field} from {schema} where ({{targetHits:{fetch_k}}}nearestNeighbor(embedding,q_embedding)) or userQuery()",
...
"input.query(q_embedding)": vec,
```

`_fetch_vespa_candidates` is the function actually used in the agentic single-match/batch-match flow (`run_agentic_match` → `_fetch_vespa_candidates`). It has **never** included the embedding-similarity clause, in either the domestic or international path — confirmed live:

```
_fetch_vespa_candidates('Aguas Mortais', market='international') -> []
_fetch_vespa_candidates('Aguas Mortais', market='domestic')      -> []
```

Direct query against Vespa for `select * from sources movie_master_intl where userQuery()` with `query: "Aguas Mortais"` returns `totalCount: 0`, even though "Deep Water" (the correct film, confirmed embedded and indexed under Vespa doc id 156728) is sitting right there in the same corpus. A same-language query for "Deep Water" against Brazil finds it instantly via plain `_db_search`. The embedding IS present and correct in Vespa — it's just never queried.

This bug **predates the international feature** and affects domestic matching identically; it is not something introduced by the international work. It only becomes highly visible for international titles because cross-language title translation is the common case there (a Brazilian ticketing page will show "Águas Mortais", not "Deep Water"), whereas domestic titles rarely need semantic-only recall.

### Contributing factor: pg_trgm can't bridge language gaps

The `_db_search` → `_trigram_search` fallback (uses Postgres `pg_trgm`/`unaccent`, default similarity threshold 0.3) is designed for spelling/accent/punctuation variance — e.g. "Oh Sukumari" vs "Oh..! Sukumari". It is not designed for, and cannot help with, genuine cross-language translation. Measured trigram similarity for the 10 title/master-title pairs I could compute cleanly:

| Input title | Correct master title | Trigram similarity | Passes 0.3 threshold? |
|---|---|---|---|
| Samurai Angel: The Complete Blood Epic | Kill Bill: The Whole Bloody Affair | 0.169 | No |
| Aguas Mortais | Deep Water | 0.000 | No |
| Los demonios del amanecer | Demons at Dawn | 0.194 | No |
| Terapia di famiglia | Jamais sans mon psy | 0.000 | No |
| C'è ancora domani | There's Still Tomorrow | 0.000 | No |
| Thousand Moons | La Mamma | 0.043 | No |
| Rufus, la serpiente que no sabia nadar | Rufus: The Sea Serpent Who Couldn't Swim | 0.180 | No |
| El anfitrión | The Birthday Party | 0.000 | No |
| Le Fabuleux destin d'Amélie Poulain | Amelie | 0.206 | No |
| Lmd Solo La Mas 2 | Father There is Only One 2 | 0.053 | No |

Every score is far below the 0.3 threshold pg_trgm needs to surface a row. This confirms trigram search was never going to solve this class of failure — only true semantic (embedding) search can, which is exactly the capability `_fetch_vespa_candidates` is missing.

## Per-title verification

Confirmed via direct query against the live `moviemasterintl` table that every title below has a correct, existing row:

| # | Input title (scraped) | Country | DB has row? | Correct DB title | movie_id |
|---|---|---|---|---|---|
| 1 | Samurai Angel: The Complete Blood Epic | Thailand | ✅ Yes | Kill Bill: The Whole Bloody Affair | 136551 |
| 2 | Aguas Mortais | Brazil | ✅ Yes | Deep Water | 140809 |
| 3 | Los demonios del amanecer | Mexico | ✅ Yes | Demons at Dawn | 128219 |
| 4 | Terapia di famiglia | Italy | ✅ Yes | Jamais sans mon psy | 128989 |
| 5 | C'è ancora domani | Italy | ✅ Yes | There's Still Tomorrow | 26286 |
| 6 | Thousand Moons | Brazil | ✅ Yes | La Mamma | 148210 |
| 7 | Rufus, la serpiente que no sabía nadar | Spain | ✅ Yes | Rufus: The Sea Serpent Who Couldn't Swim | 146459 |
| 8 | Rufus: la serpiente que no sabía nadar | Spain | ✅ Yes | Rufus: The Sea Serpent Who Couldn't Swim | 146459 |
| 9 | El anfitrión | Spain | ✅ Yes | The Birthday Party | 140430 |
| 10 | Le Fabuleux destin d'Amélie Poulain | France | ✅ Yes | Amelie | 14587 |
| 11 | Lmd Solo La Mas 2 | Mexico | ✅ Yes | Father There is Only One 2 | 148205 |

(Row count is 11 distinct films — rows 7 and 8 in the CSV are the same film, "Rufus," submitted twice with slightly different input spelling, both correctly unmatched for the identical reason.)

In every case the agent's own reasoning in the CSV independently arrived at the correct title via **web search** (IMDb/TMDB/Wikipedia/regional press), then correctly reported "no DB candidate corresponds to this" because the pre-fetch literally never handed it the row to consider. This is the intended fail-safe behavior working correctly — the bug is upstream of the LLM, not in the LLM's judgment.

## Fix plan

### Fix 1 (primary, required): Make `_fetch_vespa_candidates` do real hybrid search

Rewrite `_fetch_vespa_candidates` in `backend/app/title_matching/agentic/runner.py` to match the pattern already proven correct in `VespaSemanticIndex.search()`:

1. Embed the query title via `get_embedding()` (already used elsewhere in the codebase — Cohere Embed Multilingual v3, `search_query` input type) before building the YQL body.
2. Change the YQL to `where ({{targetHits:N}}nearestNeighbor(embedding,q_embedding)) or userQuery()`, matching `semantic_index.py`'s existing pattern.
3. Pass `input.query(q_embedding)` in the request body.
4. If embedding fails (Bedrock unavailable), fall back to the current BM25-only behavior rather than failing the whole pre-fetch — same graceful-degradation pattern used elsewhere (`get_embedding` already returns `None` on failure; just check for it).
5. This fix applies identically to both `market="domestic"` and `market="international"` — same function, same bug, same fix. No market-specific logic needed beyond what already exists (schema/id-field selection is already market-aware).

This alone should resolve all 12 rows in the CSV, since the embedding model is explicitly multilingual and the target documents are already correctly embedded and indexed in Vespa (confirmed above) — the only missing piece is sending the query vector.

### Fix 2 (defense in depth, optional): Surface `master_movie_title` as a secondary DB search target

Currently `_db_search`/`_trigram_search` only search `movie_title` (the per-country display title). Since Fix 1 should already solve retrieval via semantic search regardless of language, this is not strictly required, but as a cheap additional safety net: consider having `_fetch_db_candidates` also try an ILIKE/trigram pass against `master_movie_title` for the international path, in case a future ticketing page shows an *English* title for a market where the DB stores it under a country-local `movie_title` only. Lower priority than Fix 1.

### Not recommended: any prompt/instruction change

The agent's reasoning is correct and working as intended in every one of these 12 cases — it did real web research, identified the right film, and correctly declined to fabricate a `movie_master_id` when given no legitimate candidates. Changing the prompt would not fix anything; the gap is entirely in what candidates it's handed before it ever starts reasoning.

## Verification plan after the fix

1. Unit test: mock/patch `get_embedding` and Vespa response to prove `_fetch_vespa_candidates` now sends `input.query(q_embedding)` and the `nearestNeighbor` YQL clause, for both `market="domestic"` and `market="international"`.
2. Live re-test of all 12 rows from this CSV (single-match API, market=international, matching country) — expect all 12 to now return the correct `movie_master_id`/`movie_id` with high confidence.
3. Regression check: re-run a domestic same-language batch (e.g. the earlier "Inception" smoke test) to confirm the fix doesn't change/break existing domestic keyword-matchable results.
4. Re-run the original 75-row international batch job (or a fresh one) and confirm the no-match rate drops significantly for cross-language titles.
