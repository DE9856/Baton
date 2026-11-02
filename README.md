# Baton

Baton turns a departing member's authorised project trail into a verified, permission-safe handover. It proves what the sources can prove, asks the person leaving only about what they can't, and shows each person only what they may see.

Team sky-flyers, WCC Forge 48, Challenge 01: Prevent Critical Knowledge From Leaving With People.

**New here, or moving to a new system?** Start with [CLAUDE.md](CLAUDE.md): what's built, setup requirements, a full run-through and what's left.

## Try it

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

To go live on Supabase, Groq, GitHub and Hugging Face, follow [docs/DEPLOY.md](docs/DEPLOY.md).

### A five-minute walkthrough

Each person has their own sign-in: the **user ID is their name** and the **password is `pass`**. The bar at the top of each page says what to do next, and **Switch user** changes person.

1. **Arjun (leaving).** Open **Gap interview**. Answer by tapping, typing or recording your voice, or pass a question on. Check "What the sources say about you".
2. **Sana (reviewer).** Open **Review**. Resolve the 3 conflicts, approve routine claims in one pass, decide the risky items, then approve the version.
3. **Meera (taking over).** Open **Handover**. Open a receipt, acknowledge a section, or mark something unclear. Try **Ask Baton** with "Who renews the domain?" and with something it can't know.
4. **New member.** The same handover shows far less, because this person is cleared for public sources only. They can request access.
5. **Sana again.** On **Connect**, press **Simulate new activity**. Only the new messages are read, and one contradicts an approved date. Resolve it and approve version 2. Meera then sees what changed.

To use your own data, choose **Start your own handover**. Without an account it's a private sandbox that only your browser can open.

## What runs for real, and what doesn't yet

| Part | State |
|---|---|
| Parsers, grounding, NLI + value guard, conflicts, sensitive guard, gaps, review, handover, Ask Baton, living handover | Real, tested |
| Voice answers | Real: browser recording to local Whisper. Tested with a synthetic voice only |
| Database | SQLite by default. Postgres with row-level security is tested on local Postgres and Supabase's own database image; **not yet run on a real Supabase project** |
| Sign-in | Code uses Supabase Auth's API. Tested against a fake server only |
| Hosted extraction | Groq adapter tested against a fake server only. **No real Groq call has been made yet** |
| Demo extraction | Replays a real LLM run from the planning round |
| Uploads without a Groq key | Keyword rules. Much weaker on unseen data (see [testing](docs/TESTING.md)) |
| Public deployment | Deployment scripts are written and untested; there is no live link yet |
| Usability test, independent second dataset | **Not done.** They need other people |

## Documents

- [Architecture, data flow and technical decisions](docs/ARCHITECTURE.md)
- [Features completed and changes from the Round 1 plan](docs/CHANGES_FROM_PLAN.md)
- [Testing evidence and honest limits](docs/TESTING.md)
- [APIs, models, datasets, libraries and AI assistance](docs/DISCLOSURES.md)
- [Round 3 features and commitments](docs/ROUND3_COMMITMENTS.md)
- [Privacy, security, accessibility and responsible design](docs/PRIVACY_SECURITY_ACCESSIBILITY.md)
- [Final report draft and roadmap](docs/FINAL_REPORT.md)
- [Going live](docs/DEPLOY.md)
- [Usability test script](docs/USABILITY_TEST.md)

A silent recording of the walkthrough is in `docs/demo/walkthrough.webm`.

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
