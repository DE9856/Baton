# Baton

Baton turns a departing member's authorised project trail into a verified, permission-safe handover. It proves what the sources can prove, asks the person leaving only about what they can't, and shows each person only what they may see.

Team sky-flyers, WCC Forge 48, Challenge 01: Prevent Critical Knowledge From Leaving With People.

**Try it now:** https://baton.happyground-10a16bb3.austriaeast.azurecontainerapps.io (it sleeps when idle, so the first load takes about 7 seconds). Click **Load the demo project**, then sign in with a person's name and the password `pass`. Every page tells you whose view you are in and what to do next.

## A five-minute walkthrough

Each person has their own sign-in: the **user ID is their name** (a first name works) and the **password is `pass`**. **Switch user** at the bottom of the sidebar changes person.

1. **Arjun (leaving).** Open **Connect** and wait for "Run finished". Open **Gap interview**: answer by tapping, typing or recording your voice, or pass a question on. Check "What the sources say about you".
2. **Sana (reviewer).** Open **Review**. Resolve the 3 conflicts, approve routine claims in one pass, decide the risky items, then approve and share the version.
3. **Meera (taking over).** Open **Handover**. Open a receipt, acknowledge a section, or mark something unclear. Try **Ask Baton** with "When is the domain renewal due?" and with something it can't know.
4. **New member.** The same handover shows far less, because this person is cleared for public sources only. They can request access.
5. **Sana again.** On **Connect**, press **Simulate new activity**. Only the new messages are read, and one contradicts an approved date. Resolve it and approve version 2. Meera then sees what changed.

To use your own data, choose **Start your own handover**. Without an account it's a private sandbox that only your browser can open.

## Completed features

**The workflow, end to end**

- **Connect.** WhatsApp exports (iOS, Android, 12-hour, US and ISO formats; date order detected from the data; a report of skipped lines), Trello JSON, GitHub JSON or a live repository URL, and meeting notes (.md, .txt, .docx). Each source records who authorised it, a consent note and its audience. People are matched across sources by alias, and each person has clearances.
- **Reconstruct.** An LLM proposes small claims, one chunk of one source per call, against a JSON schema. Adapters: Groq (live), Claude, Ollama, keyword rules when no key is set, and a replay of a real recorded run for the demo.
- **Verify, in code.** Every quote is checked word for word against its source; an NLI model (`cross-encoder/nli-deberta-v3-base`) checks the quote supports the claim; a value guard checks each date, number and name appears in the evidence. Labels (Verified, Confirmed by a person, Missing, Conflict, Withheld, Needs review) are computed by rules, never from the AI's own confidence.
- **Conflicts.** Related claims are found with MiniLM embeddings, a facet check keeps genuine disagreements (different dates, opposite status, different owners) and NLI must agree. Conflicts are never resolved automatically.
- **Sensitive guard.** Secrets are masked at ingestion and never stored. Phone numbers, personal emails, health, HR and finance details are withheld from everyone but the reviewer. A pasted credential becomes a "rotate this" task.
- **Gaps and the gap interview.** Departure-cliff items, coverage against a role template and missing reasons become about 10 questions for the person leaving, answered by one tap, typing or voice, or passed to someone else. The leaver can confirm or dispute what the sources say about them. Answers are labelled as a person's word, never Verified.
- **Review.** Routine verified claims are approved in one pass; risky ones one by one. The reviewer resolves conflicts, publishes shareable versions of withheld items, grants access requests and approves immutable versions with a content hash and a diff.
- **Handover.** A per-viewer plan: Start here, Day 1 to Week 2, decisions, owners, risks, contacts, access, open questions and a timeline marked with the leaver's last day. Each line carries its receipt. Sections can be acknowledged or marked unclear, which sends a question back. Markdown export.
- **Permissions.** Each person sees only what their clearances allow, enforced in code and, on Postgres, by row-level security. The interface says how many sources are not shared with you.
- **Audit log.** Append-only record of who connected, viewed, edited, approved and shared what.

**Built ahead of Round 3**

- **Ask Baton.** Answers are the approved claims themselves, with receipts, limited to what you may see; it abstains when unsure and offers to route the question to a person.
- **Living handover.** Re-sync reads only new records, reopens an approved claim only if new evidence contradicts it, and shows the successor what changed between versions.
- **Voice answers.** Browser recording, a local Whisper draft the person corrects, and nothing counts until saved. Audio is deleted if a credential is heard.

**Platform**

- FastAPI with server-rendered pages and HTMX; one hand-written stylesheet with light and dark themes; automated WCAG 2.1 A/AA checks on the main pages.
- SQLite locally; Supabase Postgres with row-level security in production. Name-and-`pass` sign-in for demos and sandboxes; Supabase accounts for real projects.
- Docker image built by GitHub Actions; deployed on Azure Container Apps; CI runs the test suite on every push.

## What runs for real

| Part | State (2 Nov 2026) |
|---|---|
| Live deployment | Azure Container Apps, with Supabase Postgres (eu-west-1). The deployed smoke test passes: full pipeline, and 0 of 7 planted secrets leak to the successor or a new member |
| Parsers, grounding, NLI and value guard, conflicts, sensitive guard, gaps, review, handover, Ask Baton, living handover | Real, tested (248 automated tests pass) |
| Hosted extraction | Real Groq runs with `openai/gpt-oss-120b`. On a second dataset the first run found 12 of 13 facts and 1 of 3 conflicts; after fixing the conflict rules, later runs found 3, 2, 3 and 1 of 3. Details in [testing](docs/TESTING.md) |
| Demo extraction | Replays a real recorded LLM run, so the demo needs no API key; all the checks after extraction run live |
| Voice answers | Real: browser recording to local Whisper. Tested with a synthetic voice only, not yet on a real phone |
| Uploads without a Groq key | Keyword rules. Much weaker on unseen data (see [testing](docs/TESTING.md)) |
| Usability test, independent second dataset | **Not done yet.** They need other people |

## Run it yourself

```sh
python -m venv .venv
.venv/Scripts/activate            # Windows. On Linux or macOS: source .venv/bin/activate
pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
uvicorn baton.web.app:get_app --factory --port 8000
```

Open http://localhost:8000 and click **Load the demo project**. No keys are needed. The first run downloads the checking models (about 800 MB), and the first voice answer downloads a speech model (about 150 MB).

Or with Docker (image about 5.5 GB, models included):

```sh
docker build -t baton .
docker run -p 7860:7860 -e BATON_SECRET=change-me baton
```

To deploy your own copy on Supabase, Groq, GitHub and Azure, follow [docs/DEPLOY.md](docs/DEPLOY.md).

## Documents

- [Architecture, data flow and technical decisions](docs/ARCHITECTURE.md)
- [Features completed and changes from the Round 1 plan](docs/CHANGES_FROM_PLAN.md)
- [Testing evidence and honest limits](docs/TESTING.md)
- [APIs, models, datasets, libraries and AI assistance](docs/DISCLOSURES.md)
- [Round 3 features and commitments](docs/ROUND3_COMMITMENTS.md)
- [Privacy, security, accessibility and responsible design](docs/PRIVACY_SECURITY_ACCESSIBILITY.md)
- [Final report and roadmap](docs/FINAL_REPORT.md)
- [Going live](docs/DEPLOY.md)
- [Usability test script](docs/USABILITY_TEST.md)

A silent recording of an earlier version of the walkthrough is in `docs/demo/walkthrough.webm`.

## Tests

```sh
pip install -r requirements-dev.txt
pytest -m "not browser"               # unit, workflow, canary-leak tests
docker run -d -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16-alpine
BATON_TEST_POSTGRES_URL=postgresql://postgres:test@localhost:55432/postgres pytest -m "not browser"
playwright install chromium && pytest -m browser      # browser, accessibility, voice
python eval/evaluate.py                                # gold-set scores
python eval/evaluate_dataset.py eval/second_dataset/foodlink
python eval/ask_eval.py
```
