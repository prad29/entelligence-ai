# How Movie Title Matching Works

This explains, in plain language, how the system figures out which row in the
"Movie Master" database a scraped cinema listing (like "KIDSHOW: Moana (Live
Action)") actually refers to — and whether domestic (US) and international
titles are matched the same way.

## The big picture

Every match request goes through the same basic shape:

1. **Look up candidates.** The system searches the database for rows whose
   title roughly matches the input (by keyword search, and a fuzzy/semantic
   search as backup). This produces a short list — say, 5–20 possible rows.
2. **Hand the list to Claude.** Claude gets the input title, the show date,
   the ticketing URL (if any), and that candidate list, along with a big set
   of written instructions ("rules") about how to pick the right one.
3. **Claude picks one (or says "no match").** If Claude isn't confident from
   the candidate list alone, it's allowed to search the web (IMDb, Wikipedia)
   to double-check. It returns its pick as structured JSON.
4. **A few automatic checks run afterward** on top of what Claude said,
   before the final answer goes out.

There is **no rule-based fallback anymore** — every single request, domestic
or international, batch or one-at-a-time, goes through this Claude-based
process. There used to be a purely mathematical fallback (fuzzy text
matching + release-date math, no web research) but it was removed: it could
get fooled by two movies that share a title from different eras (a 90s
original vs. a 2025 remake), which only a real web search can tell apart. If
Claude's step genuinely can't be reached (the service is down, etc.), the
system now returns a clean "no match" rather than a guess.

## Is domestic and international the same pipeline?

**Mostly yes — it's the same engine, with a "market" switch, plus some
country-specific wording.** Domestic (`market=domestic`) and international
(`market=international`) both go through the exact same 4-step process
above. What changes between them:

- **Which table candidates come from** — domestic searches Movie Master;
  international searches Movie Master International, and additionally
  narrows the search to just the requested country.
- **A few extra sentences of instruction that only apply to international**
  (explained below) — mainly about which of two possible titles (a local
  translation vs. the English title) to report back.

**Everything else — the core ranking logic — is identical for both.**

## …except for the new v2 pipeline, which is domestic-only

Separately, there's now a **second, newer version of the domestic pipeline**
(called "v2") that adds a bunch of new rules on top of the shared ones —
genre, cast, director, and plot synopsis are now used as extra evidence, not
just the title. **v2 only exists for domestic right now — international
still runs the older rule set only.** So today:

| | Domestic | International |
|---|---|---|
| Shared/original rules (see below) | ✅ | ✅ |
| International-only rules (title translation, country scoping) | — | ✅ |
| v2 metadata rules (genre/cast/director/synopsis) | ✅ (new, optional endpoint) | — (not built yet) |

## The rules, spelled out

### Rules shared by domestic AND international

These apply no matter which market you're matching:

1. **Clean up the input first.** Strip promo junk like "KIDSHOW:", "$5", or
   "FLASHBACK:" off the front of the title before trying to match it.
2. **Ordinal numbers are a hard rule.** If the input says "Part 2" or "III",
   never match it to a database row for a different installment (Part 5,
   etc.) — even if the titles otherwise look similar.
3. **"Live Action" means reject animated versions.** If the input says
   "(Live Action)", throw out any candidate that's clearly the animated
   original.
4. **Poster comparison (optional).** If enabled, Claude can look at the
   candidate's poster image and compare it to what's expected — a live-action
   face vs. a cartoon, for example.
5. **Release date closeness matters.** A candidate whose release date is
   close to the actual show date is preferred over one that's far off.
6. **Not every listing is a movie — that's fine.** Sports broadcasts,
   concerts, drum-corps shows, and anniversary re-releases are all normal,
   expected entries in Movie Master. The system is told never to give up on
   finding a match just because something "isn't a movie."
7. **If genuinely nothing fits, say so.** Return "no match" (id = 0) with a
   low confidence score rather than forcing a guess.
8. **Confidence drives what happens next.** 0.90+ auto-accepts; below that
   goes to a human reviewer.

### Rules that only apply to international

1. **Prefer the local release title, not just the English one.** For a
   country like Brazil, match against the title actually used in that
   country's cinemas — which may be a translation.
2. **But when reporting a "no match" guess, use the English title**, because
   that's usually what the database searches for. And if you're unsure which
   of the two (local vs. English) the database actually stores, report
   *both* — the system will try both automatically.
3. **Everything is scoped to one country.** Candidates are pre-filtered to
   just the country the listing is from, so an Indian release and a French
   release of the same film never get mixed up.

### Rules that only apply to domestic v2 (the newest addition)

This is the new, optional pipeline. Everything above still applies; these
rules are added on top:

1. **Weigh the plot summary, director, and cast — not just the title.** A
   candidate whose synopsis clearly matches what the listing is describing
   is preferred, even over a candidate with a slightly better title match.
   Conversely, if the title matches but the genre/director/cast obviously
   *contradicts* the listing (e.g. input describes a sports broadcast, but
   the candidate is tagged "Action"), that candidate is treated as wrong.
2. **A movie with no director AND no plot summary is treated as a stub —
   disregard it.** In the real data, legitimate movies almost always have
   at least one of the two. A row with *both* completely blank usually means
   it's an incomplete/placeholder database entry, not really that movie.
3. **…unless it's genuinely expected to have neither.** Sports broadcasts
   and concert/special-event listings legitimately have no director or
   synopsis — so those two genres are explicitly exempted from rule #2.
   Every other genre (Drama, Documentary, Short Film, etc.) gets no such
   exemption.
4. **This rule is double-checked by code, not just asked of Claude.** After
   Claude answers, the system independently re-checks rule #2/#3 against the
   real database values. If Claude's pick fails the check, the system
   automatically swaps in the next-best candidate that passes — and marks
   the result for human review either way, since Claude didn't actually
   choose that swapped-in row itself.

## Examples

**Example 1 — ordinal hard rule (applies to both domestic and intl)**
Input: `"Harry Potter Part 5"`. Candidates include both "Harry Potter and
the Order of the Phoenix" (the real Part 5) and "Harry Potter and the Deathly
Hallows Part 2". Even if "Part 2" scores slightly higher on raw title
similarity for some reason, it's automatically thrown out because its
ordinal doesn't match — Part 5 always wins.

**Example 2 — international title translation**
Input (from a Brazilian ticketing site): `"Águas Mortais"`. The database's
international table actually stores this row under the English title, "Deep
Water". Claude is told: match against what's really being shown in Brazil
(the local title), but when it has to report a guess for the database to
search, report the English name — "Deep Water" — since that's what the
lookup will actually find. This exact case ("Águas Mortais" → "Deep Water")
is the real example that shaped this rule.

**Example 3 — the domestic v2 metadata rule in action**
Input: `"Local High School Football Championship"`. The database has two
old, mostly-empty candidate rows that vaguely match on title, both genre
`"Drama"`, both with no director and no synopsis filled in. Since "Drama" is
not on the exemption list, both candidates get disregarded automatically —
the system reports "no match" rather than confidently picking one of two
junk rows. Compare this to `"EPL Matchday 36: Liverpool vs Chelsea"`, genre
`"Sports"`, also with no director/synopsis — that one is fine and gets
matched normally, because Sports is explicitly exempt.
