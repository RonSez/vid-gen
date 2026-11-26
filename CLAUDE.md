# Three Women film pipeline — rules for Claude

Dima = director (approves). Claude = whole crew. Full spec: `C:\Users\ronse\Downloads\three-women-film-plan.md`.
Brief: `project/brief.md`. Use `.venv/Scripts/python` for every script.

## Money (hard rules)
- **No paid fal call until Dima explicitly says testing/spending may start.** Use `--dry-run` or `FAL_MOCK=1`.
- Before any batch: show Dima the estimate (`--dry-run`), wait for "yes", then rerun with `--yes`.
- Budget: target €400, hard cap €500 (scripts refuse). At €320 (80% of target) stop and ask → `--past-warning`.
- Recheck prices before the first real batch: `budget.py prices --refresh`. Ledger: `ledger/costs.csv`.
- If a run dies mid-way: `fal_gen.py resume` collects finished jobs (never resubmit = paying twice).

## Keys
`FAL_KEY` lives only in `.env` (gitignored). Never print, echo or log it.

## Historical honesty
- Hannah Meisel Shohat, Rachel Yanait Ben-Zvi, Hannah Chizik are real: no AI faces presented as them.
  Real faces only from archival photos. AI = atmosphere, distance, from behind, hands, places.
- Every factual claim → `research/sources.md`. Flag uncertainty to Dima.
- No anachronisms (style.yaml negative list). Check QA sheets for modern objects.

## Files
Never overwrite generated files (scripts version them: `shot_014_v2.mp4`). `shots/shots.yaml` is the
single source of truth; edit it via `scripts/shots.py` where possible.

## Workflow cheat sheet
| Dima says | Run |
|---|---|
| "014: B" | `shots.py pick 14 B` |
| "redo 22, warmer light" | `shots.py note 22 "warmer light"` → `fal_gen.py stills --shots 22` |
| "clip 14 v2 ok" | `shots.py pick-clip 14 v2` |
| "4:32 too long" | `assemble.py where 4:32` → `shots.py set <id> duration_s=…` |

Phase commands: `fal_gen.py looks` → `gallery.py looks` · `fal_gen.py stills --chapter N` → `gallery.py refs --chapter N`
· `fal_gen.py video --chapter N` → `qa_frames.py --chapter N --status draft` (Claude views sheets; auto-retry ≤2, then flag)
→ `gallery.py clips --chapter N` · `tts.py compare` → `gallery.py voices` · `tts.py scenes --scenes sc_001` (sample first)
· `kenburns.py`, `maps.py render`, `titles.py` · `assemble.py rough` · `assemble.py final --burn en`.
Status/costs: `shots.py status`, `budget.py status`, `budget.py film`. Health check: `scripts/selftest.py` (free).

## Checkpoints
Keep updates short: (a) what to look at, (b) decisions needed, (c) spend vs budget.
