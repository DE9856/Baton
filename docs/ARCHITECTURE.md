# Architecture, data flow and technical decisions

## Overview

One Python service: FastAPI, server-rendered Jinja2 pages, HTMX for progress, one hand-written stylesheet (no CSS framework), and background jobs in a thread in the same process. The NLI and embedding models load once, on CPU.

```
 Exports                   Pipeline (baton/pipeline.py)                         People
 WhatsApp .txt   ┐                                                            ┌ Leaver: gap interview
 GitHub URL/JSON ┤  parsers ─► mask secrets ─► evidence units                 │
 Trello JSON     ┤                                 │                          ├ Reviewer: review console
 Notes .md/.docx ┘                                 ▼                          │
                         1 extract (one chunk of one source; LLM or rules)    └ Successor: handover view
                         2 ground  (quote checked by code)                         ▲
                         3 support (NLI + value guard)                             │ compose per viewer
                         4 guard   (sensitive tags; credential tasks)              │
                         5 reconcile (cosine ─► facet check ─► NLI)                │
                         6 gaps    (departure cliff, coverage, routing)            │
                                   │                                               │
                                   ▼                                               │
                         Claim ledger ─► review ─► immutable Version (snapshot) ───┘
```

## Data flow, step by step

The app has three kinds of project: a demo (synthetic, open to all, where each person signs in with their name and the shared password `pass`), a sandbox (private to the browser that made it, same simple sign-in), and a member project (each person signs in with their own Supabase account).

1. **Connect** (`baton/parsers/`). Each parser returns evidence units plus a skip report, so nothing is dropped silently. WhatsApp date order is detected from the data: values above 12 first, then whichever order keeps the chat chronological. Each source records its audience, who authorised it and a consent note. People are matched across sources by alias.
2. **Mask at ingestion** (`verify/sensitive.py`). Credentials are masked before anything is stored or sent to a model, so a secret can't leak from the database or a prompt. The raw upload is never kept.
3. **Extract** (`extract/`). One chunk of one source per call, with no tools and a JSON schema. Records are presented as data, and the prompt tells the model not to follow instructions inside them. Output is a proposal only. Groq when a key is set (free-tier rate limits wait rather than fail), otherwise keyword rules; the demo replays a recorded run.
4. **Ground** (`verify/grounding.py`). A quote must match its record exactly after normalisation, or nearly verbatim with identical numbers and negations. A true quote with the wrong ID is re-anchored, but only within the chunk the model saw.
5. **Support** (`verify/value_guard.py`, `pipeline.py`). `cross-encoder/nli-deberta-v3-base` must give entailment of at least 0.5, against either the speaker + record + previous message or the speaker + exact quote. Separately, every date, number and capitalised name in the claim must appear in the evidence. Dates are resolved deterministically, anchored to the record's timestamp.
6. **Sensitive guard**. Regex rules tag phone numbers, personal emails, health, HR and finance content. Tagged claims are withheld from non-reviewers. Each record holding a credential also produces a "rotate this" task carrying only the masked text.
7. **Reconcile** (`verify/conflicts.py`). MiniLM embeddings select related pairs (cosine at least 0.35). Words in the project's own name don't count as a shared subject. The facet check keeps a pair only if the claims give different dates, opposite done/not-done status, or different owners of the same subject. NLI contradiction of at least 0.5 in either direction must then agree. Cards sharing a claim merge into one conflict. The newer side is suggested; nothing is auto-resolved.
8. **Gaps** (`verify/gaps.py`). Owner, access, deadline and next-action claims involving the leaver become departure-cliff questions, one per item. A role template raises coverage gaps. Verified decisions without a reason raise a question. Routing: the leaver gets what they alone can answer; anything from a source they can't see goes to the reviewer; open team questions go to triage. Overflow past the budget goes to the reviewer rather than being dropped.
9. **Interview and review** (`review.py`). Answers become attributed, dated testimony, labelled Confirmed by person, never Verified. Routine claims are approved in one pass; risky ones individually. Conflicts must be resolved before approval. A claim a reviewer edits or accepts after a failed check becomes that reviewer's word and loses Verified.
10. **Version and handover** (`review.py`, `handover.py`). Approval writes an immutable snapshot with a SHA-256 hash and a diff. The view is composed per viewer at read time, only from claims that viewer may see, and rendered from claims rather than generated prose.
11. **Ask** (`ask.py`). Searches only the viewer's approved items. A result needs similar meaning (cosine ≥ 0.42) and a specific word in common. Answers are the claims themselves, so nothing new is written; otherwise it abstains and offers to route the question.
12. **Re-sync** (`living.py`, `pipeline.run_incremental`). Records are matched by author, time and text. Only new ones are read. Earlier claims keep their approval unless new evidence conflicts, which reopens them. A changed or removed record sends its claims back to review.
13. **Voice** (`voice.py`). The browser records; PyAV decodes; Whisper-tiny writes a draft on the server. It counts only when the person saves it. If the transcript contains a credential, the audio is deleted.

## Trust labels

Computed by rules in `pipeline._label`; the model's confidence plays no part.

| Label | Rule | Who sees it |
|---|---|---|
| Verified | Quote grounded, entailment ≥ 0.5, all values present, not sensitive, no open conflict | Everyone cleared |
| Confirmed by person | Interview answer, or a reviewer's own word | Everyone cleared |
| Missing | An open question, always with a person to ask | Everyone cleared for its source |
| Conflict, Needs review, Possibly outdated, Withheld | Each becomes one of the above, or stays withheld | Reviewer only |

## The permission model

```
visible(claim, viewer) = claim.audience in viewer.clearances
                         AND (viewer is the reviewer OR claim is not sensitive)
```

- A claim comes from one source chunk, so it inherits exactly one audience.
- On Postgres, reads for a viewer run under `baton/rls.sql` with that viewer's identity (`permissions.rls_scope`), so the database decides. A SQL filter and a Python check then apply as well. Snapshots go through a database function that filters them, then `handover.item_visible`.
- `tests/test_postgres.py` breaks the app's own checks and confirms the database still refuses. The whole test suite also runs on Postgres.
- Being the reviewer doesn't grant clearance: a reviewer not cleared for a source can't see it.
- Viewers have read-only grants. Writes go through the app's checks on the owner connection, and `versions` and `audit_events` have immutability triggers.
- On SQLite (local use) there is no RLS, so the SQL and Python layers do the work.

## Important technical decisions

| Decision | Why |
|---|---|
| Mask secrets at ingestion rather than at display | A stored secret is a leak waiting to happen, and hosted models never need to see one |
| Facet check before NLI, not after | The result is the same intersection, but it costs no model time and allows a wider candidate net. A fixed budget of 30 pairs missed real conflicts ranked 31–35 |
| Check support against the exact quote as well as the whole record | Long records confuse the classifier. This reduced Needs review from 18 to 12 on the demo |
| Render the handover from claims, with no generated prose | Prose can drift; claims can't say more than was checked. This removes the need for the plan's containment check for now |
| Facet check before capping | With a cap on raw pairs, repeated statements crowded out real conflicts in a long chat |
| Pipeline failure clears machine output | No half-built ledger can be approved; answers and approved versions stay |
| Same-card expansion for status conflicts only | "DB backups: Done" must fall with "backup cron configured", but "in progress" stays true when sources only disagree on a date |
| Ownership answers skip the contradiction flag | "Meera takes this over" is meant to differ from "Arjun is responsible" |
| Re-runs keep people's answers | Only machine output is replaced, so nobody is asked twice |
| If the NLI model can't load, label nothing Verified | The product must not guess. Claims stay in Needs review and the UI says why |

## Code map

| Path | Purpose |
|---|---|
| `baton/parsers/` | The four core connectors |
| `baton/extract/` | Schema, prompt, Claude, Ollama, rules and replay extractors |
| `baton/verify/` | Grounding, value guard, dates, sensitive guard, conflicts, gaps, models |
| `baton/pipeline.py` | Orchestration and label rules |
| `baton/permissions.py`, `baton/rls.sql` | The visibility rule, in app code and in the database |
| `baton/auth.py` | Sign-in through Supabase Auth |
| `baton/ask.py`, `baton/living.py`, `baton/voice.py` | Ask Baton, re-sync, voice answers |
| `baton/review.py` | Interview, review, versions, acknowledgement |
| `baton/handover.py` | Per-viewer composition and export |
| `baton/web/` | FastAPI routes, templates, CSS |
| `baton/db.py` | The evidence model |
| `eval/` | Gold-set scoring, second dataset, held-out checks, scale test, deployment smoke test |
| `deploy/` | Scripts to check Supabase, publish to GitHub and deploy to Azure Container Apps (`deploy_azure.py`). `deploy_space.py` is the older Hugging Face route |

## Performance

Measured in Docker limited to 2 CPUs, on the demo's 91 records: 21 s from clicking the button to a finished pipeline, with extraction replayed; about 860 MB of memory. Hosted extraction will add model latency, not yet measured. See TESTING.md for a large chat.
