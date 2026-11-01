# Features completed and changes from the Round 1 plan

## The ten MVP features

"Working" means it runs end to end with automated tests, on both SQLite and Postgres.

| # | Feature | State | Notes |
|---|---|---|---|
| 1 | Source connectors | Working | GitHub URL or JSON, WhatsApp, Trello, notes (.md, .txt, .docx), one-click demo. GitHub URL checked once against the live API |
| 2 | Audience and consent | Working | Audience, authoriser and consent note per source; alias matching; clearances per person |
| 3 | Claim extraction | Partly | Groq and Claude adapters are tested against fake servers only. **No real hosted call yet.** The demo replays a real recorded LLM run; uploads without a key use keyword rules |
| 4 | Verification engine | Working | Grounding, NLI, value guard, rule-based labels |
| 5 | Reconciliation and timeline | Working | Conflicts, staleness, departure cliff, timeline |
| 6 | Sensitive-content guard | Working | Secrets masked at ingestion, tags, rotation tasks, shareable versions. Regex-based, so it can miss things |
| 7 | Gap interview | Working | Routing, one-tap ownership answers, "not me", testimony, leaver confirmation |
| 8 | Review console | Working | One pass, per-claim decisions, conflict resolution, access requests, versions and diff |
| 9 | Successor handover | Working | Role scope, Start here, Day-1 / Week-1 / Week-2, acknowledgement, "unclear" loop, Markdown export |
| 10 | Audit log | Working | Append-only, with a screen for the reviewer |

## Round 3 items built early

Ask Baton, Living handover and Voice gap interview are in the product, at a team member's direction. See [ROUND3_COMMITMENTS.md](ROUND3_COMMITMENTS.md), including why the team must check this with the organiser and choose three new commitments.

## Milestones

| Milestone | Exit test | State |
|---|---|---|
| M1 Skeleton | A judge can load the demo and see its records | **Passes locally and in the Docker image.** No public deployment yet |
| M2 Ledger | Verified claims with working receipts | **Passes** |
| M3 Trust and flow | Full workflow end to end on the demo | **Passes**, in a browser and in the container on Postgres |
| M4 Harden and ship | Submission complete | **Partly.** What's left is below |

## What's left, and who it needs

| Task | Needs | State |
|---|---|---|
| Fill in `.env` and run the steps in [DEPLOY.md](DEPLOY.md) | Your accounts | Scripts written, not yet run for real |
| First real Groq run, and compare with the recorded run | Groq key | Not done |
| First real Supabase run: database, sign-in, RLS | Supabase project | Tested on local Postgres and a fake sign-in server only |
| CI's first run on GitHub | Repository | Not done |
| Team reads and reviews the code; two reviewers on security code | The team | Not done |
| Independent second dataset | Someone outside the team | Brief is in `eval/second_dataset/README.md` |
| Usability test with 5 club members | Club members | Script is in [USABILITY_TEST.md](USABILITY_TEST.md) |
| Try voice on a real phone, with a real voice | A person | Not done |
| Narrated demo video | The team | Silent walkthrough recorded |
| Choose three new Round 3 commitments; check with the organiser | The team | Proposals written |
| Each member states their role and contribution | Each member | Not done |

## Changes from the plan, and why

| Plan | Now | Why |
|---|---|---|
| Claude for extraction | Groq by default when `GROQ_API_KEY` is set; Claude and Ollama adapters remain | The team chose Groq |
| Opus-class model words questions and summaries | Questions from templates; no generated prose | Rendering from claims avoids prose drift entirely |
| Containment check on prose | Not built | No prose is generated, so there's nothing to check |
| App passes each user's login token to Postgres | App sets the viewer's identity on the connection for reads (`permissions.rls_scope`) | Same effect for reads, with one connection pool. Writes go through the app's own checks |
| All reads under RLS | Reads of claims, records and handovers on a viewer's behalf | The others, such as question lists, are filtered in app code and have policies for direct access |
| Secrets masked at display | Masked at ingestion, never stored | Stronger: testing found a raw password reaching the database |
| Top ~30 pairs by similarity, then NLI, then facet | Cosine ≥ 0.35, then facet, then NLI | Real conflicts ranked past 30; the facet check is free, so it runs first |
| pgvector | Embeddings computed per viewer per question | Permission-safe by construction, and simple. Slower on very large handovers |
| dateparser | Small deterministic date reader (`verify/dates.py`) | Only a few formats needed, plus a rule for the year when none is written |
| Supabase Storage for files | Voice notes stored in the database; raw exports never kept | One less service |
| Two reviewers for security code; integrate every 6 hours | Not followed in this stage | Built in one AI-assisted session (see Disclosures). The team must review it |
| PDF export | Markdown export | PDF was in the plan's cut order |
| Telegram, Slack, PDF parsers | Not built | First in the plan's cut order |
| (not in plan) | Private sandboxes without an account | Judges can test with their own files without signing up |
