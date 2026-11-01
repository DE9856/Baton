# Testing evidence and honest limits

Measured on 1 Nov 2026. Reproduce with the commands in the README.

## Automated tests

| Suite | Tests | Covers |
|---|---|---|
| `test_parsers.py` | 32 | Demo record counts; iOS, Android, US, ISO and 12-hour WhatsApp; ambiguous dates; UTF-16; 50,000-line chat; bad files; GitHub errors |
| `test_grounding.py` | 39 | 13 adversarial quotes, date anchoring, value guard |
| `test_sensitive.py` | 29 | Masking, false positives, classification |
| `test_rules.py` | 30 | Facet check, staleness, departure cliff, routing, the permission rule, re-runs |
| `test_failures.py` | 31 | Bad uploads, API failures, crashes, the NLI model being down, restart, HTML escaping, adapters |
| `test_round3.py` | 32 | Sign-in, sandboxes, leaver confirmation, access requests, Ask, re-sync, voice uploads, Groq (fake servers) |
| `test_workflow.py` | 15 | The product over HTTP with real models, including a canary check on every page for every person |
| `test_round3_models.py` | 7 | Ask, re-sync and speech with real models |
| `test_postgres.py` | 8 | Row-level security on real Postgres, including with the app's own checks deliberately broken |
| `test_browser.py` | 2 | A first-time walkthrough in a real browser; voice with a simulated microphone; WCAG checks on 6 pages; phone width |

All pass: 215 on SQLite (the 8 Postgres tests skip there), all 223 on Postgres 16 with row-level security active, and 2 in the browser.

**Deployment check:** `eval/smoke_deployed.py` passes against the Docker image limited to 2 CPUs and connected to Postgres, with row-level security active: 21 s to run the pipeline, about 860 MB of memory.

## Not tested against the real thing

These need your credentials, so they've only run against fake servers or a local stand-in:

- **Groq:** no real call yet, so extraction quality on real output is unknown.
- **Supabase:** Postgres behaviour is tested on Postgres 16 locally; Supabase Auth only against a fake server.
- **Hugging Face Space, GitHub Actions:** the scripts and workflow have never run. The Docker image they use has.

## Gold-set evaluation

The answer key was written during the planning round: 23 facts, 3 conflicts, 7 canaries. A simulated reviewer takes the newer side of each conflict; nobody answers the interview.

| System | Facts | Conflicts | Canaries leaked to Meera | Citations |
|---|---|---|---|---|
| Round 1 generic AI summary (plan, not re-run) | 12/23 | 0/3 | 4/7 | 0 |
| Round 1 throwaway pipeline (plan) | 22/23 | 3/3 | 0/7 | 60 |
| **This product, replayed Qwen extraction** | **23/23** | **3/3** | **0/7** | 52 |
| This product, keyword rules, no AI | 21/23 | 3/3 | 0/7 | 77 |

**Read these with care:**

- **The fact score is a weak measure.** It checks that keywords co-occur on one line. Keyword rules score 21/23 largely by keeping source sentences, so this doesn't show the output is a usable handover.
- **The demo isn't independent.** It's synthetic and team-written, and both the checks and the keyword rules were developed on it. The rules' 3/3 came after adjusting them on this data; the first version found 0/3.
- **The same recorded extraction fed development and evaluation.** No new LLM output has been scored.
- Canaries are counted by exact string only.

## The second dataset: a less flattering, more honest number

`eval/second_dataset/foodlink` is a different domain, with a different chat format and some Hinglish. **It was written by the same AI that wrote the rules, so it's not independent.** It was scored once, and the raw first-run output is kept in `first_run.txt`. The rules weren't changed to raise the score, except to fix a security bug.

| Keyword rules, first run | Result |
|---|---|
| Facts | 9/13 |
| Planted conflicts | **0/3** |
| Canaries leaked to the successor | 0/5 |
| Secrets masked | **Missed one**: `billing-password Kitchen!4471` reached the reviewer and the snapshot. Fixed afterwards, with a test |

So on data it wasn't tuned on, the no-AI fallback finds much less, and in particular doesn't find conflicts. The checks still hold (no false claims, no leaks to the successor), but a judge uploading files without a Groq key gets a thin ledger. The real test is Groq on this dataset, then an independent dataset. Neither has happened.

## Other measurements

| Check | Plan | This build | Note |
|---|---|---|---|
| Support check, NLI alone | 92/102 | 92/102 | Same result from independent code |
| Support check, NLI + value guard | 99/102 | 100/102 | **No longer held out:** the guard was adjusted after seeing these errors |
| Facet check keeps conflicts NLI found | 26/26 | 25/25 | **No longer held out:** an early version kept 11/25, and the rules were then improved |
| Facet check on non-conflict pairs | 3/30 false alarms | 3/30 | At least one of those "restatements" (30 April vs 5 May) is a real conflict in the LLM-written data |
| Adversarial quotes accepted | 0/11 | 0/13 | Our own set |
| Routing | 32 raised, 10 to leaver | 17 raised, 9 to leaver | All planted gaps asked, plus the signing key |
| Review by exception | 52 / 13 / 16 | 52 / 20 / 13 | One pass / decide / needs review |
| Claims | 83, 82 grounded | 83, 82 grounded | The fabricated "library" claim is still rejected |
| Ask Baton, answerable | n/a | 17/18 | Threshold chosen on these questions, so development only. Missed "Who is the contact at college IT?" |
| Ask Baton, unanswerable | n/a | 9/10 abstained | "Is Kabir having surgery?" returned "Kabir is unavailable from 3 Nov to 17 Nov": no health detail, but it isn't an abstention |
| Canaries in any Ask answer | n/a | 0 | 3 viewers, 10 probing questions |
| Voice transcription | n/a | Runs end to end | One synthetic voice through a simulated microphone. The result varies by run: "Render deploys it" has come back as "render to deploy it" and "render to close it". That second one changes the meaning, which is why a draft counts only after the person corrects and saves it |
| WCAG 2.1 A/AA (axe-core) | n/a | No serious or critical issues on 6 pages | Automated only; no screen-reader test |

### A large chat

A generated chat of 2,000 messages, with keyword rules and real models on 2 CPU threads: parsed in 1 s; pipeline in 176 s, of which NLI took 79 s; 1,157 claims; 7 conflicts; peak Python memory 184 MB. The parser alone handles 50,000 messages in a test.

The messages are generated, so this measures speed, not quality. The rate of claims per message is much higher than real chat with keyword rules, so this is a pessimistic case. Runs happen in the background with visible progress, one at a time.

## What testing caught and changed

Found by running the product, not by code review:

| Found | Changed |
|---|---|
| A raw password reached the database through the model's `value` field | Secrets found in any field are removed from all fields of that claim |
| A new dataset's password wasn't masked (`billing-password Kitchen!4471`) | Added a rule for a password word followed by a password-shaped value |
| A crash could leave a half-built ledger | Machine output is cleared on failure, and approval is blocked until a run succeeds |
| Only 1 of 3 conflicts found | Candidate selection widened; the facet check moved first |
| In a long chat, repeated statements crowded out real conflicts | The cap now applies after the facet check, with a regression test |
| A date mentioned in passing created a false conflict | Date facets read the statement, not the whole quote |
| "November" and "said" counted as shared subjects; so did the project's name | Removed from subject matching |
| "DB backups: completed" stayed Verified after its card was proven wrong | Status claims from the same card join the conflict |
| That rule then wrongly dragged in "SSO: in progress" | Limited to status conflicts |
| Facet check kept 3/9 status and 0/8 owner conflicts on other-domain pairs | Wider status vocabulary; owners compared without needing a roster |
| "Meera takes this over" was flagged as disagreeing with "Arjun is responsible" | Ownership answers exempt |
| Testimony said "takes this over" with no idea what "this" was | Subject shown on every line |
| The API key pasted in chat raised no question | Credential tasks come from records, not from what the model chose to extract |
| The handover page was very long and listed questions twice | Plan first, reference sections collapsed, duplicates removed |
| "Run again" deleted the leaver's answers | Only machine output is replaced |
| The main button failed WCAG contrast | Colour darkened |
| The footer printed `<baton.db.Version object at 0x…>` on one page | Variable renamed, with a test |

## Known limitations

- **No real user has tried this.** Durations such as "15 minutes" are design targets; the interview page's estimate is a simple calculation.
- **Never run against real project data.**
- **Rules are tuned on one synthetic project.** Expect lower quality on real chats. The second dataset shows this.
- **Non-English messages:** the checks are English-only. Hinglish parses correctly, but with keyword rules it rarely becomes a claim, so it silently thins out the handover. Untested with Groq.
- **Voice** has only heard a synthetic voice, on desktop Chromium.
- **The sensitive guard is regex-based**, and it has already missed one password on new data. Expect more.
- **Some claims are poorly worded or mistyped**, such as "Meera will take over" under Contacts, and some open questions are noise. The reviewer can reject them.
- **No data deletion or retention limit yet.**
- **Jobs are in-process.** A restart loses a running job; the UI says so and it can be re-run. One pipeline runs at a time, so a long chat delays others.
- **Teammates found in uploads start with public clearance only** until the reviewer changes it. Safe, but it can surprise.
