# Testing evidence and honest limits

Measured on 1 Nov 2026. Reproduce with the commands in the README.

## Automated tests

| Suite | Tests | Covers |
|---|---|---|
| `test_parsers.py` | 30 | Demo record counts; iOS, Android, US, ISO and 12-hour WhatsApp formats; ambiguous dates; bad files; GitHub connector errors |
| `test_grounding.py` | 39 | 13 adversarial quotes, date anchoring, value guard |
| `test_sensitive.py` | 27 | Masking, false positives, classification |
| `test_rules.py` | 29 | Facet check, staleness, departure cliff, routing, the permission rule, re-runs |
| `test_failures.py` | 31 | Bad uploads, API failures, crashes, the NLI model being down, restart, HTML escaping, adapters with fake clients |
| `test_workflow.py` | 15 | The product over HTTP with real models, including a canary check on every page for every person |
| `test_browser.py` | 1 | A first-time walkthrough in a real browser, including phone width |

All 172 pass. In addition:

- **`db/test_rls.sh`:** 21 of 21 Row-Level Security checks pass on real Postgres 16.
- **`eval/smoke_deployed.py`:** passes against the Docker image limited to 2 CPUs.

## Gold-set evaluation

The answer key was written during the planning round: 23 facts, 3 conflicts, 7 canaries. A simulated reviewer takes the newer side of each conflict; nobody answers the interview.

| System | Facts | Conflicts | Canaries leaked to Meera | Citations |
|---|---|---|---|---|
| Round 1 generic AI summary (plan, not re-run) | 12/23 | 0/3 | 4/7 | 0 |
| Round 1 throwaway pipeline (plan) | 22/23 | 3/3 | 0/7 | 60 |
| **This product, replayed Qwen extraction** | **23/23** | **3/3** | **0/7** | 52 |
| This product, keyword rules, no AI | 21/23 | 0/3 | 0/7 | 94 |

**Read these numbers with care:**

- **The fact score is a weak measure.** It checks that keywords co-occur on one line. Keyword rules scored 21/23 by copying sentences verbatim, so this does not show that the output is a usable handover. Conflicts and leaks are the more telling columns.
- **The data is synthetic and written by the team**, and the rules were developed against it.
- **The same recorded extraction fed development and evaluation.** No new LLM output has been scored.
- The canary score counts exact strings only.

## Other measurements

| Check | Plan | This build | Note |
|---|---|---|---|
| Support check, NLI alone | 92/102 | 92/102 | Same result from independent code |
| Support check, NLI + value guard | 99/102 | 100/102 | **No longer held out:** the guard was adjusted after seeing these errors |
| Facet check keeps conflicts NLI found | 26/26 | 25/25 | **No longer held out:** an early version kept 11/25 here, and the rules were then improved. NLI flagged one fewer here, possibly because statements are compared differently |
| Facet check on non-conflict pairs | 3/30 false alarms | 3/30 | At least one of those "restatements" (30 April vs 5 May) is a real conflict in the LLM-written data |
| Adversarial quotes accepted | 0/11 | 0/13 | Our own set, in `test_grounding.py` |
| Routing | 32 raised, 10 to leaver | 17 raised, 9 to leaver | All planted gaps asked, plus the signing key |
| Review by exception | 52 / 13 / 16 | 52 / 20 / 13 | One pass / decide / needs review. More to decide, because same-card and credential items now count |
| Claims | 83, 82 grounded | 83, 82 grounded | The fabricated "library" claim is still rejected |
| NLI time for a handover | ~33 s | 12 s here; 21 s for the whole pipeline in Docker | Different hardware |

## What testing caught and changed

These came from running the product, not from code review:

| Found | Changed |
|---|---|
| A raw password reached the database through the model's `value` field | Secrets found in any field are removed from all fields of that claim |
| Only 1 of 3 conflicts found | Candidate selection widened; the facet check moved first |
| A date mentioned in passing ("the 12 Sep notes") created a false conflict | Date facets read the statement, not the whole quote |
| "November" counted as a shared subject between unrelated dates | Months removed from subject matching |
| "DB backups: completed" stayed Verified after its card was proven wrong | Status claims from the same card join the conflict |
| That rule then wrongly dragged in "SSO: in progress" | Limited to status conflicts |
| Facet check kept 3/9 status and 0/8 owner conflicts on the other-domain pairs | Wider status vocabulary; owners compared without needing a roster |
| "Meera takes this over" was flagged as disagreeing with "Arjun is responsible" | Ownership answers exempt |
| Testimony said "takes this over" with no idea what "this" was | Subject shown on every line |
| The API key pasted in chat raised no question | Credential tasks come from records, not from what the model chose to extract |
| The handover page was very long and listed questions twice | Plan first, reference sections collapsed, duplicates removed |
| "Run again" deleted the leaver's answers | Only machine output is replaced |

## Known limitations

- **No real user has tried this.** Durations such as "15 minutes" are design targets; the interview page's time estimate is a simple calculation.
- **Never run against real project data**, and never with a real hosted LLM.
- **Rules are tuned on one synthetic project.** Expect lower quality on real chats. Non-English and Hinglish messages are untested.
- **Sign-in is a stand-in.** Anyone with the link can act as anyone. App-level permission checks only protect against an honest viewer until Auth is connected.
- **The sensitive guard is regex-based.** It catches common credential shapes, not everything, and the masking of ordinary words after "password:" relies on the value looking like a password. The upload screen says so.
- **Some claims are poorly worded or mistyped**, such as "Meera will take over" filed under Contacts. This comes from the recorded local model and may improve with a hosted one.
- **Some open questions are noise**, such as "can I help with the reports part?". The reviewer can mark them not relevant.
- **Non-demo teammates start with public clearance only** until the reviewer changes it. This is safe but can surprise.
- **Jobs are in-process.** A restart loses a running job; the UI says so and it can be re-run. One pipeline runs at a time.
- **Data lives on the container's disk**, so a Space restart loses it until Postgres is connected. The demo reloads with one click.
