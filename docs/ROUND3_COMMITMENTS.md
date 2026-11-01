# The three improvements committed for Round 3

These are the plan's commitments (§8.1), unchanged. None is built yet. Each has a checkable definition of done.

## 1. Ask Baton

Cited, permission-scoped questions and answers for the successor, which abstain when evidence is missing.

- Search only the viewer's visible claims, using embeddings stored beside their audience labels (pgvector), so search can't bypass permissions.
- Every sentence of an answer must be entailed by the claims it cites (the plan's containment check). Sentences that fail are dropped.
- With no support, Baton says it doesn't know and offers to route the question. The answer later returns as testimony.

**Done when:** on a test set of answerable and unanswerable questions, every answer cites visible claims; unanswerable ones abstain; the canary test extends to answers with 0 leaks.

## 2. Living handover

The handover stays current after day 1.

- Re-sync GitHub live and accept re-uploaded exports, matching records so unchanged ones aren't reprocessed.
- Re-verify only the affected claims, and raise new conflicts against the approved version.
- Show the reviewer and successor what changed since approval, then approve a new version with a diff.

Today's re-run keeps answers but replaces all machine claims, and the diff compares wording. This commitment makes both incremental and precise.

**Done when:** adding new records to the demo updates only the affected claims, a new contradiction appears as a conflict, and the diff shows exactly that change.

## 3. Voice gap interview

Mobile-first answers by voice note.

- Record in the browser, transcribe, and let the leaver correct the transcript before it counts.
- Keep the audio as evidence behind the testimony, with the same audience and audit rules.
- Mask credentials and apply the sensitive guard to transcripts.

**Done when:** a leaver can answer all questions by voice on a phone, the answers appear as Confirmed by person with playable audio for those cleared, and a usability test compares time against typing.
