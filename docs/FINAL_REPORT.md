# Baton: final report (draft)

For the team to check and complete. Bracketed items need real information that only the team has. Don't fill them with estimates.

## The problem

When a member leaves a project, the files stay and the context goes: why decisions were made, what's half-done, who to call, which account renews next week. In our scan of 77 student tech-club repositories, one person wrote most of the commits in 56%, and 57% of READMEs never say how the site is deployed.

## Users

Student and volunteer software teams first: turnover is built in, nobody owns handover, and members can already export their data. Four people matter in each handover: the leaver, the successor, a reviewer, and the teammates who otherwise absorb the cost.

## The workflow

1. **Connect** exports members may make: GitHub, WhatsApp, Trello, notes. Each records its audience and who authorised it.
2. **Reconstruct:** AI (or keyword rules) proposes small claims, each with a word-for-word quote.
3. **Verify:** code checks each quote, a separate classifier checks support, and a value guard checks each date, number and name. Rules then find conflicts, gaps, secrets and what lapses on the leaver's last day.
4. **Interview:** the leaver gets only what they alone can answer, by tap, text or voice.
5. **Review:** a reviewer approves routine claims in one pass, decides risky ones individually, and resolves conflicts.
6. **Handover:** the successor gets a plan limited to what they may see, each line with a receipt. They can ask questions, acknowledge sections and flag what's unclear. New activity can be synced later, and only what changed needs review.

The distinctive idea is the order: prove what the evidence can prove, then ask the leaver only about what it can't. We also look forward from the last day, at the "departure cliff".

## Technology

One Python service (FastAPI, Jinja2, HTMX) in one Docker container. Postgres with row-level security on Supabase. DeBERTa NLI, MiniLM and Whisper run locally on CPU. Extraction uses Groq when a key is set. Details: [ARCHITECTURE.md](ARCHITECTURE.md).

## Evidence

See [TESTING.md](TESTING.md). In short, on our synthetic demo: 3 of 3 planted conflicts and 0 of 7 confidential items leaked to the successor. On a second dataset we wrote but didn't tune against, with keyword rules only: 9 of 13 facts, 0 of 3 conflicts, no leaks to the successor, and one password that masking missed and we then fixed. That second result is the honest picture of the no-AI fallback. [Add: Groq result on the second dataset.]

## Impact

Targets from the plan, **not yet measured**: successor at least 2× faster on 10 standard questions, leaver effort under 20 minutes, successor confidence of at least 4 out of 5. [Add: results from `USABILITY_TEST.md`.]

## Known limitations

- Rules were developed on one synthetic project; real chats will be harder.
- Non-English and mixed-language chats are largely untested.
- Voice is tested with a synthetic voice only.
- No independent security review.
- Scale: about 3 minutes of checks for a generated 2,000-message chat on 2 CPU threads. It works in the background, but runs go one at a time.

Full list: [TESTING.md](TESTING.md) and [PRIVACY_SECURITY_ACCESSIBILITY.md](PRIVACY_SECURITY_ACCESSIBILITY.md).

## Roadmap

| When | What | Why |
|---|---|---|
| Next | Mixed-language support; annual rotation with many roles; live connectors | See [ROUND3_COMMITMENTS.md](ROUND3_COMMITMENTS.md) |
| 3 months | Pilot with 3–5 college clubs at the end-of-year change of leadership; delete-my-project and retention limits; faster checks for long chats | Real data is the only real test |
| 6–12 months | Workplace edition (SSO, organisation roles, Slack and Teams); on-premise, local-only deployment | Same workflow, stricter settings |
| Long term | Approved handovers become a searchable organisational memory; an open-source core for volunteer communities | Each handover starts the next |

## Participants and contributions

Names as on the Round 1 plan, to be confirmed letter by letter. **Each member writes their own contribution (rule 14).**

| Name | Role | Contribution |
|---|---|---|
| Deepesh Kumar Kotta | Team Lead, AI & Verification | [own words] |
| Balaji Keerthi | Backend & Data | [own words] |
| Aravindh Balaji | Frontend & UX | [own words] |
| Abdul Hakeem K | Trust, Security & Product | [own words] |

AI assistance is disclosed in [DISCLOSURES.md](DISCLOSURES.md).

## Demo

Live link: [add after deployment]. Repository: [add after publishing]. A silent recording of the walkthrough is in `docs/demo/walkthrough.webm`; a narrated demo video is still to be recorded.
