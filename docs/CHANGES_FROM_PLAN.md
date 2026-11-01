# Features completed and changes from the Round 1 plan

## The ten MVP features

"Working" means it runs end to end and has automated tests. It runs on the stand-ins listed under Changes.

| # | Feature | State | Notes |
|---|---|---|---|
| 1 | Source connectors | Working | GitHub URL or JSON, WhatsApp, Trello, notes (.md, .txt, .docx), one-click demo. GitHub URL checked once against the live API |
| 2 | Audience and consent | Working | Audience, authoriser and consent note per source; alias matching; clearances per person |
| 3 | Claim extraction | Partly | Pipeline, schema and prompt done. The Claude adapter has **never called the real API**. The demo replays a recorded LLM run; uploads use keyword rules without a key |
| 4 | Verification engine | Working | Grounding, NLI, value guard, rule-based labels |
| 5 | Reconciliation and timeline | Working | Conflicts, staleness, departure cliff, timeline. Superseded decisions are handled only through conflicts |
| 6 | Sensitive-content guard | Working | Secrets masked at ingestion, tags, rotation tasks, shareable versions |
| 7 | Gap interview | Working | Routing, one-tap ownership answers, "not me", testimony with an advisory flag |
| 8 | Review console | Working | One pass, per-claim decisions, conflict resolution, versions and diff |
| 9 | Successor handover | Working | Role scope, Start here, Day-1 / Week-1 / Week-2, per-section acknowledgement, "unclear" loop, Markdown export |
| 10 | Audit log | Working | Append-only, with a screen for the reviewer |

## Milestones

| Milestone | Exit test | State |
|---|---|---|
| M1 Skeleton | A judge can load the demo and see its records | **Passes locally.** Docker image built and tested; CI written but not yet run on GitHub; no deployment, no Supabase |
| M2 Ledger | Verified claims with working receipts | **Passes** |
| M3 Trust and flow | Full workflow end to end on the demo | **Passes**, including in a browser and in the container |
| M4 Harden and ship | Submission complete | **Partly.** Canary tests and export done. Remaining work is below |

## Still to do before Round 2 submission

These need the team, credentials or other people. None can be finished from code alone.

- [ ] **Deploy:** create the Hugging Face Space (Docker type) and push. Needs an account.
- [ ] **Publish the repository** and run CI on GitHub for the first time.
- [ ] **Connect Supabase:** credentials, then move the app from SQLite to Postgres, connect Auth, and pass each user's token through so RLS applies. Largest remaining technical task.
- [ ] **Run Claude for real:** add a key, then compare recall, cost and latency with the recorded local model, as the plan promised.
- [ ] **Second dataset** written by someone outside the team, to re-validate the facet check and thresholds.
- [ ] **Usability test** with 5 club members. Not done; no user has touched this yet.
- [ ] **Demo video.**
- [ ] **Each member confirms their role and personal contribution** (rule 14).

## Changes from the plan, and why

| Plan | Now | Why | Status |
|---|---|---|---|
| Supabase Postgres + RLS | SQLite; RLS written and tested on Postgres but not connected | No credentials during this stage of the build | Temporary |
| Supabase Auth | "Viewing as" picker, no password | Same. Also lets judges switch roles, which the plan wanted | Temporary. **Not safe for real data** |
| Hugging Face Space | Runs locally and in Docker | No account or token yet | Temporary |
| Claude extraction everywhere | Demo replays a real Qwen2.5-7B run; uploads use rules without a key | No API key; the demo should work for judges at no cost | Replay stays for the demo |
| Claude Haiku 4.5 | Default `claude-haiku-5-5` | Newer and cheaper, as the plan's adapter allowed. Configurable | Permanent, unverified until a key exists |
| Opus-class model words questions and summaries | Questions from templates; no generated prose | Rendering from claims avoids prose drift entirely | Permanent for the MVP |
| Containment check on prose | Not built | No prose is generated, so there is nothing to check | Returns with Ask Baton in Round 3 |
| Secrets masked at display | Masked at ingestion, never stored | Stronger guarantee: a test found a raw password reaching the database | Permanent |
| Top ~30 pairs by similarity, then NLI, then facet | Cosine ≥ 0.35, then facet, then NLI | Real conflicts ranked 31–35 in this build; the facet check is free, so it runs first | Permanent |
| pgvector | Embeddings computed per run, not stored | No search feature needs them until Ask Baton | Returns in Round 3 |
| Two reviewers for security code; integrate every 6 hours | Not followed in this stage | This stage was built in one AI-assisted session (see Disclosures) | The team must review it |
| dateparser | Small deterministic date reader (`verify/dates.py`) | It needs only a few formats, plus a rule for the year when none is written | Permanent |
| PDF export | Markdown export | PDF is in the plan's cut order | Cut |
| Telegram, Slack, PDF parsers | Not built | First in the plan's cut order | Cut |
| "Access request instead of a dead link" (§12) | Not built | Receipts show the quote, and uncleared sources are hidden, so there are no dead links, but also no request flow | Round 3 candidate |
| Leaver confirms what is said about their work | The leaver can read the ledger, without a formal confirm step | Not reached | Open |
