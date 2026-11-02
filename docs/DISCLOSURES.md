# Disclosures

Required by the brief (rules 5, 6, 7 and 10, and "Submission Integrity").

## AI assistance

**This code was written by an AI coding tool.** Anthropic's Claude Code (model Claude Opus 5.5) wrote the code, tests and documents in this repository, in one session on 1 Nov 2026, directed by a team member and following the team's Round 1 plan. Commits carry a `Co-Authored-By: Claude` line. That session includes the features the plan listed for Round 3.

The team member confirmed that the official build window was open before this work began, and chose to build the Round 3 features at the same time.

The team remains responsible for this work and must be able to explain any part of it (rule 16). Personal contributions must be stated accurately by each member (rule 14); this file does not state them.

The product itself uses AI as described below.

## Work created before this build

No code from the planning round was copied. The repository began empty (see the first commit). The design comes from the plan, and the code was written fresh. These **data files** were prepared during the planning round and are reused:

| File | What it is |
|---|---|
| `baton/demo/data/` | The synthetic CampusConnect dataset. Written by the team with AI help; all names, messages, numbers and keys are invented |
| `baton/demo/recorded_extraction_qwen25.json` | Claims output by Qwen2.5-7B on that dataset on 30 Oct 2026, with verdict fields removed. Replayed by the demo |
| `eval/gold_campusconnect.json` | The answer key |
| `eval/heldout/` | LLM-written test statements and claim pairs. No longer strictly held out (see TESTING.md) |

Written during this build: `eval/second_dataset/foodlink/` (a stopgap dataset by the same AI, not independent), the four scripted messages in `baton/demo/data/update_whatsapp.txt`, and `tests/data/speech_sample.wav` (generated with Windows text-to-speech).
| Fact patterns in `eval/evaluate.py` | The planning round's scoring patterns, re-typed so the scores stay comparable |

## AI models

| Model | Use | Runs | Licence |
|---|---|---|---|
| `cross-encoder/nli-deberta-v3-base` | Support and contradiction checks | Local, CPU | Apache 2.0 |
| `sentence-transformers/all-MiniLM-L6-v2` | Selecting related claims | Local, CPU | Apache 2.0 |
| `openai/whisper-tiny` | Transcribes voice answers into an editable draft | Local, CPU | MIT |
| Qwen2.5-7B | Produced the recorded demo extraction; optional local mode through Ollama | Offline | Check the model's own licence before relying on it |
| A Groq-hosted model (default `openai/gpt-oss-120b`, configurable; `llama-3.3-70b-versatile` was the earlier default until Groq retired it) | Extraction of uploads when `GROQ_API_KEY` is set. First real run on 2 Nov 2026, on the second dataset | Hosted, Groq API | The chosen model's and Groq's terms (gpt-oss: Apache 2.0) |
| Claude (`claude-haiku-5-5`) | Optional alternative extractor. **Not yet used in any run** | Hosted, Anthropic API | Anthropic terms |

## External services

| Service | Use |
|---|---|
| GitHub REST API | Reads public or token-readable issues and PRs when a user enters a repository. Read-only; a token is used once and never stored |
| Hugging Face Hub | Model downloads at build time or first run |
| Groq or Anthropic API | Only if a key is configured. Receives one chunk of one source per call, with recognised credentials already masked |

Written for, but not yet run against: Supabase (Postgres and Auth), Hugging Face Spaces, GitHub Actions.

## Libraries and templates

FastAPI, Starlette, Uvicorn, Jinja2, python-multipart, itsdangerous, SQLAlchemy, psycopg, python-dotenv, PyAV (with its bundled FFmpeg libraries), RapidFuzz, NumPy, python-docx, httpx, anthropic, PyTorch, Transformers, sentencepiece, protobuf, tiktoken, huggingface_hub, pytest, Playwright and axe-core (vendored in `tests/vendor/`, MPL 2.0, test only), under their own licences. Exact versions are in `requirements.txt` and `requirements-dev.txt`.

Vendored in `baton/web/static/`: Pico.css 2.0.6 and htmx 2.0.4 (both MIT), and the fonts Barlow Condensed and Atkinson Hyperlegible, including its mono cut (SIL Open Font License). The Docker base image is `python:3.11-slim`; RLS tests use `postgres:16-alpine` and `supabase/postgres:15.8.1.060`.

No project template or boilerplate was used.

## Data

All demonstration data is synthetic. The GitHub connector was checked once against a public repository (`pallets/markupsafe`), reading 8 items; none of that data is stored in this repository.
