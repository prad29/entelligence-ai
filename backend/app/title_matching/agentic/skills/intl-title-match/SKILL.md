---
name: intl-title-match
description: >-
  Rules and worked examples for matching a scraped international cinema
  listing to a Movie Master International row - anniversary/re-release
  arithmetic, country-scoped candidate hygiene, and candidate-selection
  discipline. Mandatory for every market=international title-match request.
---

# International Title Matching

NOTE ON DELIVERY: this file is the authored source of truth for the rules
below. It is NOT loaded via Claude Code's skill auto-discovery inside
claude-sandbox — a live smoke test confirmed that headless `claude --print -`
sessions discover the file (it appears in the CLI's own `skills` listing) but
do not actually load its content into context, via either explicit
`/intl-title-match` invocation or implicit auto-discovery. Its body is
therefore inlined verbatim into the international system prompt by
`prompt_builder.py` (see `_INTL_SKILL_CONTENT`) — edit this file, then keep
that constant in sync.

## Scope

This applies only to `market=international` requests. The DB candidate list
handed to you is already scoped to the request's country (MovieMasterIntl is
filtered by `country` at fetch time). `movie_master_id` in your output must
always be an id taken from the supplied candidate list, or `0` if none fit.

## A. Anniversary / re-release resolution

**Arithmetic, not memory.** Compute the anniversary number explicitly:

```
A = year(show_date) - year(original theatrical release)
```

Never copy an ordinal out of a DB row's title text or a press mention without
recomputing `A` yourself — a "50th Anniversary" row from last year's campaign
is not this year's anniversary.

**Date-proximity rule** for deciding whether to prefer a candidate anniversary
row over the plain main-title row. Let `D` = absolute difference in days
between the candidate's `release_date` and the input's `show_date`:

- `D <= 90` days, the release_date is not a placeholder, and the candidate's
  labeled ordinal (if any) is `A` or `A-1` -> **prefer the anniversary/
  re-release row.** Confidence up to 0.96, AUTO_ACCEPT is fine.
- `D > 180` days, OR the ordinal mismatches by more than 1, OR the
  release_date is a placeholder (`0000-*`, or a suspiciously round
  `YYYY-01-01` that doesn't match any real release-date pattern for that
  franchise) -> **do not pick it.** Fall back to the plain main-title row.
  Cap confidence at 0.85, decision REVIEW.
- `90 < D <= 180` days -> ambiguous. Pick the main title, cap confidence at
  0.70, and say why in reasoning.
- Every candidate has a NULL release_date -> there is no date signal at all.
  Never auto-pick one "representative" row via relevance score alone — that
  is not a matching signal. Set `rerelease_lookup_title` to the expected
  anniversary title (e.g. "Shrek 25th Anniversary") and cap confidence low
  (REVIEW), so a human or the post-lookup can resolve it properly.
- No anniversary/re-release row exists among the candidates at all, but the
  arithmetic says one should -> set `movie_master_id: 0`,
  `rerelease_lookup_title` to the anniversary form of the title, and still
  populate `movie_title` with the plain main title as a fallback.

**A studio anniversary is not the film's anniversary.** A campaign label like
"Sony 100th Anniversary" on a Spider-Man row refers to the studio's own
anniversary, not the film's — the ordinal test (`A` / `A-1`) does not apply
to it at all. Only the date-proximity test decides whether that specific
dated row is the right pick.

**A format/audience variant is not an anniversary marker.** Tags like "Sing
Along", "OV" (original version / original-language audio), "D-BOX", "2.W."
(second week), "engl.OmU" (English with subtitles) describe *how* a listing
is screened, not a distinct edition warranting its own DB row. They never
justify preferring an anniversary/re-release candidate on their own.

### Worked examples

**A1 - accept a dated re-release row.** Input: "Harry Potter und der Orden
des Phoenix", Germany, show_date 2026-08-29. Candidates include id 162113
"Harry Potter and the Order of the Phoenix Re-Release", release_date
2026-08-28 (`D=1` day) — plus the plain 2007 original and an unrelated 1-4
bundle. `A = 2026-2007 = 19`, no clean ordinal label on the re-release row
itself, but `D=1` day is decisive on its own. Correct: pick 162113, confidence
~0.96, AUTO_ACCEPT.

**A2 - accept an anniversary row with an exact ordinal + date match.** Input:
"Harry Potter and the Philosopher's Stone (1)", Germany, show_date
2026-08-28. `A = 2026-2001 = 25`. A candidate titled "...25th Anniversary"
with release_date within a day of show_date exists — pick it, confidence
~0.97.

**A3 - reject a stale anniversary row, fall back to main title.** Input:
"Ritz On Screen: The Rocky Horror Picture Show (1975) With After Party!", UK,
show_date 2026-08-22. Candidates: a "...50th Anniversary" row with
release_date 2025-09-26, and a plain 2012 row. `A = 2026-1975 = 51`, not 50 —
the "50th" row is last year's campaign. `D` between 2025-09-26 and
2026-08-22 is ~330 days, far past the 180-day ambiguity ceiling. Correct:
pick the plain row, cap confidence at 0.85, REVIEW, and say explicitly in
reasoning that the anniversary row is stale (wrong year, `D` too large).

**A4 - studio-anniversary label, decide on date alone.** Input:
"Spider-Man (2002)", UK, show_date 2026-08-07. A candidate row is labeled
"Spider-Man (2002) - Sony 100th Anniversary" — "100th" is Sony-the-studio's
anniversary, not the film's (the film would be turning `2026-2002=24`), so
the ordinal test never applies here. Whether to pick this row depends purely
on whether its release_date sits within ~90 days of the 2026-08-07 show_date
(a real theatrical re-release campaign) versus being a stale placeholder — if
close, prefer it over the plain title as the more specific, currently-active
listing; if it's not close or is a placeholder, fall back to the plain title.

**A5 - indistinguishable NULL-date duplicates, never guess by relevance.**
Input: "Shrek - The daring hero", Germany, show_date 2026-09-12. Ten
near-identical "Shrek" rows exist, all with NULL release_date and
indistinguishable relevance scores. `A = 2026-2001 = 25`. There is no date
signal to disambiguate which row (if any) corresponds to this specific
2026 anniversary screening. Correct: do NOT pick "the first" or "the
highest-relevance" row as if that were meaningful — set
`rerelease_lookup_title: "Shrek 25th Anniversary"`, confidence <= 0.60,
REVIEW.

**A6 - anniversary row absent from candidates entirely.** Input:
"Harry Potter und der Stein der Weisen - 25 jähr. Jubiläum", Germany,
show_date 2026-08-28 (also seen as the OV/English-audio variant, and again
in Spanish as "[25 aniversario]"). None of the pre-fetched candidates are
even the right franchise (they're unrelated semantic noise). `A = 25`.
Correct: `movie_master_id: 0`, `movie_title` = the plain main title (for the
US/English rule) or the appropriate localized/English pair, AND
`rerelease_lookup_title: "Harry Potter and the Sorcerer's Stone 25th
Anniversary"` so the post-lookup has a second, more specific string to try.

**A7 - format variant, not an anniversary marker.** Input: "Vaiana: Sing
Along", Spain, show_date 2026-07-29. "Sing Along" is a format note on the
live-action "Moana (2026)" release, not evidence of a separate anniversary
edition. Correct: map to the plain main title "Moana (2026)" (or per
existing product instruction, to whatever the confirmed main-title row is),
not to an unrelated franchise's sing-along rows.

## B. Candidate hygiene notes (also used for the independent verification pass)

These rules exist because reasoning has been observed to correctly identify
the real film via research, then still emit the wrong id/title, or emit
`movie_master_id: 0` with a blank output despite a confident identification.
A separate lightweight verification step (outside this sandbox call) re-checks
your pick against these same rules — following them here reduces how often
that step needs to override you.

- `movie_title` in your output MUST be the literal stored title string of the
  candidate row whose id you emit — never a paraphrase or a different string
  than what that row's `movie_title` field actually contains.
- A placeholder or implausible `release_date` on a candidate (e.g. a
  suspiciously round `YYYY-01-01` that doesn't match the film's real release
  pattern) weakens that candidate — it does not make the field neutral or
  irrelevant. Say so in reasoning if you still pick it, and lower confidence.
- Never break a tie between two or more indistinguishable candidates using
  relevance score alone — that is not evidence of correctness, only of
  retrieval order. Prefer REVIEW over a confident-sounding coin flip.
- When you are confident in a film's real-world identity (via web research)
  but no candidate id fits, ALWAYS still populate `movie_title` (and
  `alternate_movie_title` / `rerelease_lookup_title` where relevant) so the
  downstream post-lookup has something concrete to search for — do not leave
  the identification "in your reasoning" only. A correct identification with
  `movie_master_id: 0` and a blank `movie_title` is a worse outcome than the
  same identification with a populated title, even though both start at id 0.

### Worked examples

**B1 - title/id mismatch despite correct reasoning.** Input:
"(engl.OmU)Die Odyssee 2.W. (also in D-BOX)", Germany. Reasoning correctly
and confidently concludes this is Christopher Nolan's "The Odyssey" (2026).
The only candidate is id 25331 titled "Odyssey" with an implausible
placeholder release_date (2025-01-01, a year before the film's real release).
The failure: emitting `movie_title: "Odyssey"` (dropping "The") while pointing
at a weak, placeholder-dated candidate, and calling it high confidence.
Correct handling: either report the title exactly as the candidate row
stores it (verify the literal string) while explicitly flagging the
placeholder date and capping confidence — or, if the placeholder date makes
you doubt the row entirely, report `movie_master_id: 0` with `movie_title:
"The Odyssey"` for post-lookup instead of forcing a weak match to look
strong.

**B2 - unjustified relevance tie-break.** Same as A5 (Shrek): do not resolve
ties between indistinguishable NULL-date duplicates by picking "the first" or
"the highest relevance" candidate.

**B3 - correct identification, wrongly reported as a total miss.** Input:
"Madame", France, show_date 2026-07-29. Reasoning correctly identifies this
as Hélène Rosselet-Ruiz's film (international title "Madame", French release
title "Le Triangle d'or"), confirmed via multiple sources including the exact
release date. None of the pre-fetched candidates are this film (they're
unrelated "Madame Web"/"Madame Hofmann" hits). The failure: emitting
`movie_master_id: 0` with a BLANK `movie_title`. Correct: still emit
`movie_title: "Madame"` and `alternate_movie_title: "Le Triangle d'or"` so
the post-lookup can search both — a confident identification should never
produce an empty title field.

**B4 - confirm, don't second-guess a clean match.** Input: "Harry Potter und
der Orden des Phoenix" (same as A1) — a `D=1`-day dated re-release row is
about as clean a match as this task gets. Do not manufacture doubt; pick it
confidently.

**B5 - tie-break with a correctable signal.** Input: "Dirty Dancing",
Belgium, show_date 2026-08-28. Two plain (non-anniversary, non-sequel,
non-spinoff) candidates exist for the same 1987 film. Do not resolve this by
"highest Vespa relevance" — instead prefer whichever candidate is actually
scoped to the request's country (see Section C); if both are, cap confidence
to reflect the genuine ambiguity rather than picking one arbitrarily.

**B6 - correct NO-MATCH with both titles populated.** Input: "Couscous und
Geheimnisse", Germany. Research correctly identifies the German title
"Couscous und Geheimnisse" as the German release of the French film "La
Petite Cuisine de Mehdi" (English: "Spices and Lies" / "Couscous and Other
Secrets"). None of the pre-fetched candidates are this film. Correct:
`movie_master_id: 0`, `movie_title` set to the English/canonical title, and
`alternate_movie_title` set to the German localized title — both populated,
not left blank.

## C. Country-aware candidate hygiene

Every candidate you are given now carries a `country` field (both the DB and
Vespa candidate lists). Apply these rules before reasoning about title
similarity at all:

- A candidate whose `country` differs from the request's country is
  disqualified outright. Name it as discarded in your reasoning rather than
  silently ignoring it — this is useful signal for downstream review, not
  just for you.
- A semantic (Vespa) hit that shares zero real lexical or plot relation to
  the input — matched only on a stray shared word, format tag, or edition
  marker (e.g. "Anniversary", "Sing-Along", a single word like "Todes") — is
  noise, not a candidate. Never let its presence justify picking it, and
  never let it be the sole basis for setting `movie_master_id: 0` without
  also reporting the correct English + localized titles per Section B.

### Worked examples

**C1 - cross-country duplicates plus staleness.** Input: "Dirty Dancing",
Belgium (see B5). Candidates for the same title exist tagged with other
countries, plus a stale "35th Anniversary" row and an unrelated "Movie Party"
themed-screening row. Filter to Belgium-scoped candidates first, then apply
the A3-style staleness test to any remaining anniversary row before falling
back to the plain title.

**C2 - pure semantic noise, zero overlap.** Input: Harry Potter 25th-
anniversary German listings (see A6) where every returned candidate is an
entirely unrelated title ("Der Frosch und das Wasser") with no lexical,
plot, or franchise overlap with Harry Potter at all — matched only by weak
semantic proximity. Discard all of them; do not let their presence lower your
confidence in the correct `rerelease_lookup_title` fallback.

**C3 - false match on a shared edition-marker word.** Input: "Chihiros Reise
ins Zauberland - 25 Anniversary" (Spirited Away, German title), Germany. The
only candidates returned are "Titanic 25 Year Anniversary" rows — matched
purely on the shared token "Anniversary", not on any real relation to
Spirited Away or Ghibli. An edition marker is never a title-match signal on
its own; discard these and report the correct film via the id=0 path.

**C4 - false match on a shared format tag.** Input: "Vaiana: Sing Along"
(see A7). If the only candidates returned are "Frozen Sing-Along" rows,
matched purely on the format tag "Sing-Along" and sharing nothing else with
Moana/Vaiana, discard them — do not treat a shared format tag as a title
match.

**C5 - false match on a single shared word.** Input: "Harry Potter und die
Heiligtümer des Todes - Teil 1" (Deathly Hallows Part 1), Germany. If a
candidate like "Auris - Die Frequenz des Todes" is returned, it was matched
on the single word "Todes" ("death") and has no other relation to Harry
Potter — discard it as noise.

## Output contract

In addition to the standard output schema, for `market=international`
requests you may also populate:

- `alternate_movie_title` — a second title guess (localized vs.
  English/master) for the id=0 post-lookup to try, per the existing rule.
- `rerelease_lookup_title` — the anniversary/re-release form of the title
  (e.g. "Shrek 25th Anniversary") when Section A's arithmetic says a dated
  row should exist but none of the supplied candidates plausibly represent
  it. `null` otherwise. This is tried as the FIRST id=0 post-lookup attempt,
  ahead of `movie_title`/`alternate_movie_title`, since it is the most
  specific guess when present.
