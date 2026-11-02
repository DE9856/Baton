# Features completed and changes from the Round 1 plan

## The ten MVP features

"Working" means it runs end to end with automated tests, on both SQLite and Postgres.

| # | Feature | State | Notes |
|---|---|---|---|
| 1 | Source connectors | Working | GitHub URL or JSON, WhatsApp, Trello, notes (.md, .txt, .docx), one-click demo. GitHub URL checked once against the live API |
| 2 | Audience and consent | Working | Audience, authoriser and consent note per source; alias matching; clearances per person |
| 3 | Claim extraction | Working | Groq runs for real (`openai/gpt-oss-120b`; 12 of 13 facts on the second dataset's first run). The Claude adapter is tested against a fake server only. The demo replays a real recorded LLM run; uploads without a key use keyword rules |
| 4 | Verification engine | Working | Grounding, NLI, value guard, rule-based labels |
| 5 | Reconciliation and timeline | Working | Conflicts, staleness, departure cliff, timeline |
| 6 | Sensitive-content guard | Working | Secrets masked at ingestion, tags, rotation tasks, shareable versions. Regex-based, so it can miss things |
| 7 | Gap interview | Working | Routing, one-tap ownership answers, "not me", testimony, leaver confirmation |
| 8 | Review console | Working | One pass, per-claim decisions, conflict resolution, access requests, versions and diff |
| 9 | Successor handover | Working | Role scope, Start here, Day-1 / Week-1 / Week-2, acknowledgement, "unclear" loop, Markdown export |
| 10 | Audit log | Working | Append-only, with a screen for the reviewer |

## Round 3 items built early

Ask Baton, Living handover and Voice gap interview are in the product, at a team member's direction. See [ROUND3_COMMITMENTS.md](ROUND3_COMMITMENTS.md), including why the team must check this with the organiser and choose three new commitments.

## Hosting changed from the plan

The plan named Hugging Face Spaces, with Railway as the paid fallback. In November 2026 Hugging Face began requiring a paid PRO subscription for Docker Spaces, so the Space could not be created on a free account. Baton runs instead on **Azure Container Apps** under Azure for Students credit, in Austria East (the allowed region nearest the Supabase database in Ireland). It is the same Dockerfile and the same image, built by GitHub Actions and published to `ghcr.io/de9856/baton`. Groq's original default model, `llama-3.3-70b-versatile`, had also been retired, so the default is now `openai/gpt-oss-120b`.

## Milestones

| Milestone | Exit test | State |
|---|---|---|
| M1 Skeleton | A judge can load the demo and see its records | **Passes**, locally, in the Docker image and live on Azure Container Apps (https://baton.happyground-10a16bb3.austriaeast.azurecontainerapps.io) since 2 Nov 2026 |
| M2 Ledger | Verified claims with working receipts | **Passes** |
| M3 Trust and flow | Full workflow end to end on the demo | **Passes**, in a browser and in the container on Postgres |
| M4 Harden and ship | Submission complete | **Partly.** What's left is below |

## What's left, and who it needs

| Task | Needs | State |
|---|---|---|
| Fill in `.env` and run the steps in [DEPLOY.md](DEPLOY.md) | Your accounts | **Done 2 Nov 2026.** Live on Azure; the deployed smoke test passes |
| First real Groq run, and compare with the recorded run | Groq key | Second dataset run on 2 Nov with `openai/gpt-oss-120b`: 12/13 facts, 1/3 conflicts. Comparison with the recorded demo run not done |
| First real Supabase run: database, sign-in, RLS | Supabase project | **Done 2 Nov 2026.** `deploy/check_supabase.py` passes on the hosted project (eu-west-1) |
| CI's first run on GitHub | Repository | **Done 2 Nov 2026.** Passes on `main` and `round2-build` |
| Team reads and reviews the code; two reviewers on security code | The team | Not done |
| Independent second dataset | Someone outside the team | Brief is in `eval/second_dataset/README.md` |
| Usability test with 5 club members | Club members | Script is in [USABILITY_TEST.md](USABILITY_TEST.md) |
| Try voice on a real phone, with a real voice | A person | Not done |
| Demo video | The team | Only if the organiser asks for one; a silent walkthrough of an earlier interface exists |
| Choose three new Round 3 commitments; check with the organiser | The team | Chosen 2 Nov 2026: multilingual chats (Hinglish, Hindi, Tamil, Telugu, Kannada), annual rotation, live connectors. Organiser check still to do |
| Each member states their role and contribution | Each member | Done: in [FINAL_REPORT.md](FINAL_REPORT.md) |

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
