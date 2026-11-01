# Round 3 features and commitments

## Decision for the team to record

The plan's three Round 3 commitments were built during the same session as the Round 2 work, at a team member's direction, so they are already in the product. Two things follow, and **the team must settle both**:

1. **Check the rules.** Rule 1 counts only work done in the official window for the applicable event. Confirm with the organiser that this work counts, and be open about when it was built: the commit history shows it.
2. **Round 2 still needs three commitments for Round 3.** They must be substantial and undelivered. Below are three proposals. These are drafts for the team to choose from, and none is built.

## The plan's three improvements: delivered

### 1. Ask Baton
Cited, permission-scoped answers that abstain when evidence is missing; unanswerable questions become routed questions.

| The plan's definition of done | State |
|---|---|
| Every answer cites visible claims | Yes. Answers are the approved claims themselves, with receipts |
| Unanswerable questions abstain | 9 of 10 on our set (see TESTING.md); the miss is noted there |
| Canary test extended to answers: 0 leaks | Yes, for 3 viewers and 10 probing questions |
| A question can be routed to someone | Yes |

Differs from the plan: answers are extractive, so no LLM writes a new sentence and no containment check is needed. Search uses embeddings computed per viewer from that viewer's items, not pgvector, so search can't bypass permissions, but it recomputes per question.

### 2. Living handover
| The plan's definition of done | State |
|---|---|
| New records update only the affected claims | Yes. Records are matched by author, time and text; 4 new of 49 read |
| A new contradiction appears as a conflict | Yes. A later date reopens the approved claim |
| A diff shows that change | Yes, for the reviewer and, filtered, for each viewer |

Works for re-uploaded exports and GitHub re-sync. Removed or edited records send their claims back to review. Not automatic: someone presses the button.

### 3. Voice gap interview
| The plan's definition of done | State |
|---|---|
| Answer by voice on a phone | Works in Chromium on desktop with a simulated microphone. **Not yet tried on a real phone** |
| Shown as Confirmed by person, with audio for those cleared | Yes |
| Leaver corrects the transcript first | Yes. A draft counts only when saved |
| Credentials masked in transcripts | Yes. If a credential is found, the audio itself is deleted |
| Usability test against typing | **Not done** |

Tested with a synthetic voice only. Whisper-tiny makes mistakes ("render to deploy it"), which is why the person corrects it. Accented or Hinglish speech is untested.

## Proposed new commitments for Round 3

Taken from the plan's future scope (§10), each aimed at a weakness our testing found.

### A. Works on the way Indian student teams really write
Hinglish, Hindi and Tamil chats through extraction and verification. Today the checks are English-only, and non-English claims tend to fall to Needs review, so the handover silently thins out.

**Done when:** on a labelled mixed-language chat, verified recall reaches an agreed target, and untranslated claims are clearly marked rather than lost.

### B. Annual rotation: many handovers at once
One handover per role from shared sources, with role templates (tech lead, events lead, treasurer) and a club-level view of who has handed over what. This is scenario 3 in the plan, and it's when real clubs need this most.

**Done when:** a demo club hands over three roles from one set of sources, each role gets its own coverage questions and view, and a shared fact approved once needn't be approved again.

### C. Live connectors with consent
Connect GitHub, Google Drive and Notion directly, with per-source consent, instead of exports, plus Telegram export and PDF parsing. This removes the biggest manual step and makes the living handover run by itself.

**Done when:** a team connects a Drive folder and a repository, changes appear as a reviewable diff without re-uploading, and revoking consent removes the source and sends its claims back to review.
