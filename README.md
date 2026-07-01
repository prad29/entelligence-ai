# MovieQ — Amenity Screen Format Detector

**➡️ Build spec to hand to Claude Code: [`CLAUDE_CODE_BUILD_BRIEF.md`](CLAUDE_CODE_BUILD_BRIEF.md)**
It is self-contained — the full "what to build", detection logic, seed-data compilation, edge cases,
and acceptance tests are all inside it. The only external input it needs is the seed spreadsheet
`data/Amenities Priority.xlsx`. None of the other files here are required to build the app.

## Folder map

| Path | What it is |
|---|---|
| `CLAUDE_CODE_BUILD_BRIEF.md` | **The authoritative, self-contained build brief.** Start here. |
| `data/` | Seed spreadsheet `Amenities Priority.xlsx` (the app's only data input), the compiled `screen_format_mapping.json`, and the uploaded reference spec. |
| `docs/` | Supporting design docs — `BUSINESS_LOGIC.md` (deep logic write-up) and `BusinessLogic_Comparison.md` (reconciliation vs. the uploaded spec). |
| `reference-engine/` | A working Python reference implementation that **validates** the logic. Validation only — not needed to build the app. |
| `analysis/` | One-off analysis: ambiguity mining + the team-review workbook (`Amenity_Ambiguity_Review.xlsx`). |
| `ui-preview/` | `AmenityDetector_UI_Preview.jsx` — the shadcn-style UI mockup. |

## Optional: run the reference engine to validate

```bash
python3 reference-engine/build_mapping.py     # compile seed xlsx → data/screen_format_mapping.json
python3 reference-engine/test_core.py         # 24 edge-case tests (all pass)
```
