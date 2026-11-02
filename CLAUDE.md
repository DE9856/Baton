# Baton: project handoff

Read this first. It says what needs doing, what exists, how to set it up on a new system, how to run through it, and what's left. Claude Code loads this file automatically; people can read it too.

## What needs to be done for submission

**Round 2 is not submittable yet.** It's roughly 70% done, and Round 3 roughly 60%. These percentages are a judgment as of 1 Nov 2026, not a measurement. The code is close to finished; most of what's left needs accounts or other people.

**Where we stopped (2 Nov 2026):** Supabase and Groq are paused on purpose, to come back to later. No Baton project exists on Supabase yet, and Groq has no key. See "Resuming Supabase" in section 7.

**Do these in order:**

- [ ] **1. Ask the organiser** for the deadlines, and whether Round 3 work built now counts. This could affect eligibility (rules 1 and 4). Also confirm the Round 1 plan was actually submitted: the PDF is dated 31 Oct, but the team's checklist still listed it as a to-do. *On 2 Nov a team member indicated that Round 3 work counts; record who confirmed it and when. The deadlines and the Round 1 submission are still open.*
- [ ] **2. Fill in `.env`** with Groq, Supabase, Hugging Face and GitHub (section 7), then deploy and publish with `docs/DEPLOY.md`. These steps have never run for real, so expect some fixes. *GitHub and Hugging Face don't have to wait for Supabase and Groq. `deploy/deploy_space.py` currently requires the Supabase values, so a demo-only deploy needs a small change first: no real accounts, data lost on restart, and only the app's own permission checks, all of which must be disclosed.*
- [ ] **3. Run Groq on the second dataset** as soon as the key is in: `python eval/evaluate_dataset.py eval/second_dataset/foodlink`. This is the biggest unknown. Keyword rules found 0 of 3 conflicts there, so a weak result may need fixing before submission.
- [ ] **4. Choose three new Round 3 commitments.** The original three are already built. Proposals are in `docs/ROUND3_COMMITMENTS.md`.
- [ ] **5. Have the team read the code.** Judges can question any member on any part (rule 16). Each member confirms their role and writes their own contribution (rule 14).
- [ ] **6. Run the usability test** with 5 club members if time allows (`docs/USABILITY_TEST.md`). If time is short, shorten this rather than the second dataset. Never invent results.

**Round 2 checklist, from the brief:**

| Required item | Status | What's left |
|---|---|---|
| Working product | Mostly done | Full workflow runs on the demo; real Groq and Supabase have never been run |
| Deployed link or demo for judges | **Not done** | Scripts are written but never run; needs Supabase and Hugging Face credentials |
| Repository judges can access | **Not done** | Everything is committed locally; needs a GitHub repo and token |
| Architecture and technical document | Done | `docs/ARCHITECTURE.md` |
| APIs, models, datasets and libraries disclosed | Done | `docs/DISCLOSURES.md` |
| Features completed, changes from plan, testing evidence | Done | The usability test would strengthen the evidence |
| Three Round 3 commitments | **Decision needed** | The team chooses; three proposals are drafted |

**Round 3 checklist:**

| Item | Status |
|---|---|
| The three improvements | Built; voice is untested on a real phone or with a real voice |
| Testing notes, privacy and security report, limitations, roadmap | Written; update them after the first real runs |
| Final report (`docs/FINAL_REPORT.md`) | Draft; needs real results added |
| Demo video | Silent recording only (`docs/demo/walkthrough.webm`); needs narration |
| Participant and contribution details | **Not done**; each member writes their own |

**Baton** turns a departing member's authorised project trail into a verified, permission-safe handover. It proves what the sources can prove, asks the person leaving only about what they can't, and shows each person only what they may see.

Team sky-flyers (CMR Institute of Technology, Bengaluru), WCC Forge 48, Challenge 01: Prevent Critical Knowledge From Leaving With People. Last updated 2 Nov 2026, version 0.3.0, branch `round2-build`.

## 1. Status in brief

- The whole workflow runs on the demo, in a browser and in Docker: connect, reconstruct and verify, gap interview, review, handover.
- The Round 3 features are built too: Ask Baton, Living handover, Voice answers.
- **Not live yet.** There is no public link. Real Groq, real Supabase, Hugging Face and GitHub Actions have never been used, because no credentials have been supplied yet. Section 7 covers this.
- **No real person has used it.** The usability test and an independent test dataset are still to do.

## 2. What you need on a new system

| Need | Detail |
|---|---|
| Python | **3.11** (tested on 3.11.9). PyTorch 2.5.1 has no build for 3.13 |
| Disk | About 4 GB: packages 1.7 GB, models about 1 GB, Chromium for browser tests about 0.3 GB |
| Memory | About 1 GB while running. No GPU needed |
| Internet | For the first install and first run, which download packages and models |
| Git | To keep the history. `.git` is the evidence of when the work was done, so don't delete it |
| Docker | Optional. Only for the image and the Postgres tests |
| Accounts | None to run the demo. Section 7 lists them for going live |

**Windows: keep the folder path short**, such as `C:\baton`. Some `transformers` files have very long names, and past Windows' 260-character path limit `pip` fails with a confusing `No such file or directory` error, leaving a broken install. If you've hit it, delete `.venv`, move the folder and start again.

## 3. Setup

Unzip the project, open a terminal in its folder, then run the following. The `.venv` folder isn't in the zip, because a virtual environment doesn't move between machines.

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
uvicorn baton.web.app:get_app --factory --port 8000
```

**Linux or macOS:**

```sh
python3.11 -m venv .venv
source .venv/bin/activate
pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
uvicorn baton.web.app:get_app --factory --port 8000
```

On macOS, drop the `--index-url` part: PyTorch's default build for Mac is already CPU-capable. Windows is the only system this has actually been run on, apart from the Linux Docker image.

Install PyTorch first, as shown. Otherwise `pip` pulls the much larger GPU build.

Open http://localhost:8000. The first run downloads the two checking models, so the first demo takes a few minutes; after that it takes about 20 seconds. The first voice answer downloads a speech model (about 150 MB).

To check the install:

```sh
pip install -r requirements-dev.txt
pytest -m "not browser"         # expect 237 passed, 11 skipped
```

A fresh clone was tested this way on 1 Nov 2026.

**Using Claude Code on the new system:** open Claude Code in this folder. It reads this file. A good first message is "Set this up and run the tests". Docker Desktop isn't needed for that.

**Sending it on:** commit your work, then run this from the project folder. It needs only Python and git, and writes `baton-handoff.zip` next to the project folder:

```sh
python deploy/make_zip.py
```

Use this rather than zipping by hand or with `tar`: exclude rules for the local `data` folder also removed the demo dataset in `baton/demo/data` when tested. The script takes exactly what git tracks, plus the history in `.git` and `docs/reference/`, and leaves out `.venv`, `.env` and local data.

The zip contains team members' phone numbers (in the plan PDF), so **share it only with the team**. Send `.env` separately and privately if the other person needs it.

## 4. Run-through

Click **Load the demo project**. Every person signs in separately: the **user ID is their name** (a first name works, and case doesn't matter) and the **password is `pass`**. **Switch user** at the top right changes person. The bar at the top of each page says what to do next.

| User ID | Who | Sees |
|---|---|---|
| Arjun Mehta | Leaving | Public and tech-team sources |
| Meera Iyer | Taking over | Public and tech-team sources |
| Sana | Reviewer | Everything, including sensitive items |
| Kabir Shah, Riya Thomas | Teammates | Public and tech-team sources |
| New member | Just joined | Public sources only |

Follow these in order. The numbers are what you should see on the demo.

1. **Sign in as Arjun.** Open **Connect**. He sees 5 of the 6 sources, and a line saying one more exists, because he isn't cleared for the core-committee notes. Wait for "Run finished": 83 claims, 82 with a real quote, 3 conflicts, 9 questions.
2. **Gap interview, still Arjun.** There are 9 questions. On the domain question, click **Meera takes it over**. Type an answer to the deployment question, or use **Record a voice answer**. Open **What the sources say about you** and confirm or dispute a claim.
3. **Reconstruct and verify.** Filter by label and open a receipt. "The library will open at 8 am" shows as No real quote, because the recorded model copied it from its prompt and code rejected it.
4. **Switch to Sana. Review.** It says "Not ready to share yet". Resolve the 3 conflicts (login redirect, SSO date, backups). Click **Approve all 52**. Decide the risky items: **Keep withheld** on phone numbers, the health detail and the password, **Approve** on the others. Click **Approve and share version 1**.
5. **Switch to Meera. Handover.** Lines show as Verified, Confirmed by Arjun Mehta or Missing. Open a receipt and try **See both sides** on a conflict. Acknowledge a section. Mark something unclear; it becomes a new question.
6. **Ask Baton, still Meera.** "When is the domain renewal due?" gives a cited answer. "Which payment gateway did we choose?" gives Not known, with an offer to send the question on.
7. **Switch to New member.** The handover shows about 10 items and no `campusconnect.in`. On Connect, request access. Sana can grant it in Review.
8. **Switch to Sana. Connect, then Simulate new activity.** "4 new record(s), 45 unchanged". In Review a fourth conflict appears: the domain's approved 20 Nov against a new 5 Dec. A pasted password is masked and becomes a rotate task. Decide them and approve version 2.
9. **Switch to Meera.** The handover opens with "What changed in version 2".
10. **Switch to Sana. Audit log.** All of the above is recorded.

**To try your own files:** on the home page, choose **Start your own handover**. Without an account it's a private sandbox only this browser can open, with the same name-and-`pass` sign-in. Without a Groq key, it uses keyword rules, which are much weaker on unseen data (section 6).

## 5. What's implemented

**The workflow:**

- **Connectors:** WhatsApp export (iOS, Android, 12-hour, US, ISO; date order detected from the data; a report of skipped lines), Trello JSON, GitHub JSON or live URL, notes (.md, .txt, .docx), one-click demo.
- **Audience and consent:** each source records its audience, who authorised it and a note. People are matched across sources by alias. Each person has clearances.
- **Extraction:** one chunk of one source per call, with a JSON schema. Adapters: Groq (used when `GROQ_API_KEY` is set), Claude, Ollama, keyword rules (no key), and a replay of a real recorded LLM run for the demo.
- **Verification:** word-for-word quote checks by code; NLI support check (`cross-encoder/nli-deberta-v3-base`); a value guard for each date, number and name; labels computed by rules only.
- **Conflicts:** MiniLM picks related claims, a facet check keeps different dates, opposite status or different owners, and NLI must agree. Never auto-resolved.
- **Sensitive guard:** secrets are masked at ingestion and never stored; phones, personal emails, health, HR and finance are withheld from non-reviewers; credentials become rotate tasks.
- **Gaps:** departure-cliff questions, coverage against a role template, missing reasons, routed to the leaver, reviewer or triage.
- **Gap interview:** one-tap ownership, free text, voice, "pass it on", and leaver confirm or dispute. Answers are labelled testimony, never Verified.
- **Review:** routine claims in one pass, risky ones individually, conflict resolution, shareable versions of withheld items, access requests, immutable versions with hash and diff.
- **Handover:** per-viewer view with Start here, Day 1 to Week 2 plan, decisions, owners, risks, contacts, access, open questions and timeline; acknowledgement; "unclear" loop; Markdown export.
- **Audit log:** append-only.

**Round 3 features, already built:**

- **Ask Baton:** answers are the approved claims themselves, with receipts, and it abstains when unsure.
- **Living handover:** re-sync reads only new records, reopens approved claims only if contradicted, and shows what changed.
- **Voice answers:** browser recording, then a local Whisper draft. It counts only once saved. Audio is deleted if a credential is heard.

**Platform:**

- SQLite by default. Postgres with row-level security (`baton/rls.sql`), applied automatically on start.
- Sign-in: name and `pass` on demos and sandboxes; Supabase accounts on real projects.
- Docker image, GitHub Actions workflow, deployment scripts.
- The relay-themed interface: track, baton, lane markings, torn-edge receipts. WCAG 2.1 A/AA automated checks on the main pages.

## 6. Tests and what they show

```sh
pip install -r requirements-dev.txt
pytest -m "not browser"                     # 237 passed, 11 skipped
playwright install chromium
pytest -m browser                           # 2 passed: walkthrough, voice, accessibility
docker run -d -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16-alpine
BATON_TEST_POSTGRES_URL=postgresql://postgres:test@localhost:55432/postgres pytest -m "not browser"   # 248 expected
python eval/evaluate.py                     # gold set: 23/23 facts, 3/3 conflicts, 0/7 leaks
python eval/evaluate_dataset.py eval/second_dataset/foodlink
python eval/ask_eval.py
python eval/smoke_deployed.py http://localhost:8000
```

On PowerShell, set the variable with `$env:BATON_TEST_POSTGRES_URL = "..."` first.

**Don't oversell these.** Details are in `docs/TESTING.md`:

- 23/23 facts is on data the rules were tuned on, and the scoring is lenient: plain keyword rules score 21/23.
- On a second dataset the rules weren't tuned on, keyword rules got 9/13 facts and **0/3 conflicts**. The same AI wrote that dataset, so it isn't independent either.
- The regex guard missed a password on that first run. It's fixed, but it will miss others.

## 7. Going live

Copy `.env.example` to `.env`, fill it in, and follow `docs/DEPLOY.md`. **Never paste credentials into chat or commit them.** `.env` is git-ignored.

| Service | Variables | For |
|---|---|---|
| Groq | `GROQ_API_KEY`, `BATON_EXTRACT_MODEL` | Real extraction of uploads. Check the model is on Groq's current list |
| Supabase | `BATON_DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY` | Database and sign-in |
| GitHub | `GITHUB_TOKEN`, `GITHUB_REPO` | `python deploy/publish_github.py` |
| Hugging Face | `HF_TOKEN`, `HF_SPACE` | `python deploy/deploy_space.py` |

**Resuming Supabase.** The code is ready and has passed all 248 tests on Supabase's own database image, but it has never run on a hosted project, so its pooler, network speed, REST API exposure and real sign-in are untested. A first attempt to create the project through Supabase's API on 2 Nov got a 403, because the personal access token was limited to one existing project, `dayflow-hrms`, and couldn't see the organisation. Either:

- Create the project by hand and fill in the three values, following `docs/DEPLOY.md`; or
- Generate a new access token covering the organisation, add it to `.env` as `SUPABASE_ACCESS_TOKEN`, and create the project through the API. Delete the token afterwards, and remove that line from `.env`. Don't touch `dayflow-hrms`, which is a separate project.

`.env` may hold a leftover `SUPABASE_DB_PASSWORD` line from that attempt. It belongs to no project and can be replaced. Keep Confirm email on, for the reason in `docs/DEPLOY.md`.

After filling in Supabase, run `python deploy/check_supabase.py`. Run each deploy script with `--dry-run` first. Expect some fixes, since none of this has been run for real. Then run `python eval/evaluate_dataset.py eval/second_dataset/foodlink` with the Groq key: that's the most important number not yet known.

## 8. What's left

**Needs credentials:** first real Groq run and comparison with the recorded run; first real Supabase run; deploy; first CI run.

**Needs people:**
- Usability test with 5 club members (`docs/USABILITY_TEST.md`). Never invent results.
- A second dataset from someone outside the team (`eval/second_dataset/README.md`).
- Voice on a real phone, with a real voice and real accents.
- A narrated demo video. A silent one is at `docs/demo/walkthrough.webm`.
- The team reads the code; two reviewers on permissions, the guard and the verifier.
- Each member writes their own contribution in `docs/FINAL_REPORT.md` and confirms names letter by letter.

**Decisions for the team:**
- **Round 3 timing.** Round 3 features were built on 1 Nov at a team member's direction. Confirm with the organiser that this counts under rules 1 and 4.
- **New Round 3 commitments.** Round 2 must name three undelivered improvements. Proposals are in `docs/ROUND3_COMMITMENTS.md`.

**Known gaps in the product:** no project deletion or retention limit; English-only checks; one pipeline at a time, in-process; a very long chat takes minutes (about 3 for 2,000 messages on 2 CPU threads).

## 9. Rules for working on this project

From the brief (`docs/reference/Knowledge_Handover_Problem_Statement.pdf`) and the plan (`docs/reference/sky-flyers_Baton_Round1_Plan.pdf`). Breaking the first few can disqualify the team.

- **Build window.** A team member confirmed on 1 Nov 2026 that the official window was open. The window's dates aren't in any file. If you're working much later, check the current round's rules before building.
- **Disclose AI, reused data and services** (rules 6, 7): keep `docs/DISCLOSURES.md` true. Anything you add goes there.
- **Never invent** user research, usability results or quotes. Never state a contribution for a team member.
- **Demo data must be synthetic, public, permissioned or anonymised.** Don't ask for real confidential chats.
- **Don't publish `docs/reference/`.** The plan PDF has team members' phone numbers. It's git-ignored; keep it that way.
- **Honest numbers.** Report failures plainly, and don't call tests "held out" once rules have been tuned on them. Update `docs/TESTING.md` when numbers change.
- **Trust rules in the product:** the AI never judges itself; a person's word is never labelled Verified; nothing reaches the successor without review.

**Working style the team wants:** test things for real before recommending them; check screens with screenshots, not only tests; give plain status updates.

**Practical notes:**
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commit only when asked to, or as part of an agreed build.
- Check a fresh `git clone` before calling something shippable: the clean-install test is what found that the demo data was missing from git.
- In Git Bash on this team's Windows machines, heredocs containing quotes break. Write scripts to files.
- Never run `chrome.exe --version`: it hangs.
- `tests/conftest.py`'s `fresh_db_url` runs every test on Postgres when `BATON_TEST_POSTGRES_URL` is set.

## 10. Where things are

| Path | What |
|---|---|
| `baton/parsers/` | Connectors |
| `baton/extract/` | Groq, Claude, Ollama, rules, replay |
| `baton/verify/` | Grounding, value guard, dates, sensitive guard, conflicts, gaps, models |
| `baton/pipeline.py` | Full and incremental runs, label rules |
| `baton/permissions.py`, `baton/rls.sql` | The visibility rule, in code and in the database |
| `baton/review.py`, `baton/handover.py` | Interview, review, versions; per-viewer view |
| `baton/ask.py`, `baton/living.py`, `baton/voice.py`, `baton/auth.py` | Ask Baton, re-sync, voice, sign-in |
| `baton/web/` | Routes, templates, CSS, bundled fonts |
| `baton/demo/` | Synthetic dataset and recorded extraction |
| `tests/`, `eval/`, `deploy/` | Tests, measurements, deployment scripts |
| `docs/` | Architecture, changes from plan, testing, disclosures, Round 3, privacy and security, final report draft, deployment, usability script |
| `docs/reference/` | The brief and the Round 1 plan (not in git) |
