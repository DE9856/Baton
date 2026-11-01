# Baton

Baton turns a departing member's authorised project trail into a verified, permission-safe handover. It proves what the sources can prove, asks the person leaving only about what they can't, and shows each person only what they may see.

Team sky-flyers, WCC Forge 48, Challenge 01: Prevent Critical Knowledge From Leaving With People. Round 2 build, in progress.

## Try it

```sh
python -m venv .venv
.venv/Scripts/activate            # Windows. On Linux or macOS: source .venv/bin/activate
pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
uvicorn baton.web.app:get_app --factory --port 8000
```

Open http://localhost:8000 and click **Load the demo project**. No API key is needed. The first run downloads the two checking models (about 800 MB).

Or with Docker (image about 5 GB, models included):

```sh
docker build -t baton .
docker run -p 7860:7860 -e BATON_SECRET=change-me baton
```

### A five-minute walkthrough

The purple bar on each page says what to do next. Use **Viewing as** at the top right to change person.

1. **Arjun (leaving).** Open **Gap interview**. Answer a question or two, or pass one on with "Not me".
2. **Sana (reviewer).** Open **Review**. Resolve the 3 conflicts, approve the routine claims in one pass, decide the risky items, then approve the version.
3. **Meera (taking over).** Open **Handover**. Open a receipt on any line, acknowledge a section, or mark something unclear. Try the conflict "See both sides".
4. **New member.** The same handover shows far less, because this person is cleared for public sources only.
5. **Sana again.** The **Audit log** records all of it.

To use your own data, choose **Start your own handover** and upload a WhatsApp export, a Trello JSON, a GitHub JSON or meeting notes, or enter a GitHub URL.

## Current state

The full workflow runs on real parsers, real NLI and embedding models, and real rules. Some parts use stand-ins until credentials exist:

| Part | Now | Plan |
|---|---|---|
| Extraction in the demo | Replay of a real LLM run from the planning round | Claude |
| Extraction of uploads | Keyword rules; Claude if `ANTHROPIC_API_KEY` is set | Claude |
| Database | SQLite, with permissions enforced in app code twice | Supabase Postgres with RLS |
| Sign-in | A "Viewing as" picker with **no password** | Supabase Auth |
| Hosting | Runs locally and in Docker; **no public deployment yet** | Hugging Face Space |

**Don't put real project data on a public deployment until Supabase Auth is connected:** anyone with the link can choose any person. The Claude adapter has been tested against a fake client only, never the real API.

See [docs/CHANGES_FROM_PLAN.md](docs/CHANGES_FROM_PLAN.md) for the full list.

## Documents

- [Architecture, data flow and technical decisions](docs/ARCHITECTURE.md)
- [Features completed and changes from the Round 1 plan](docs/CHANGES_FROM_PLAN.md)
- [Testing evidence and honest limits](docs/TESTING.md)
- [APIs, models, datasets, libraries and AI assistance](docs/DISCLOSURES.md)
- [The three Round 3 commitments](docs/ROUND3_COMMITMENTS.md)

## Tests

```sh
pip install -r requirements-dev.txt
pytest -m "not browser"               # unit, workflow and canary-leak tests
playwright install chromium && pytest -m browser
python eval/evaluate.py               # gold-set scores per viewer
sh db/test_rls.sh                     # Postgres policies (needs Docker)
```

Configuration is in [.env.example](.env.example).
