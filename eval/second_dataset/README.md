# A second dataset

The plan promises a second dataset **written by someone outside the team**, because the rules were developed against the first. That has not happened yet.

`foodlink/` is a stopgap: a project in a different domain, with a different chat format and some Hinglish. **It was written by the same AI that wrote the rules, so it isn't independent.** It was written once. Its first keyword-rules run and first Groq run were recorded before any change; after the Groq run (2 Nov 2026) the conflict rules were changed using what it showed, so it is no longer held out.

## How to get an independent one

Send the person this page. They shouldn't look at Baton's code or rules first.

1. Invent a small project with people, a person leaving, and a person taking over. Use made-up names and numbers only.
2. Write 40 to 100 records across at least two of: a WhatsApp export (`.txt`), a Trello JSON, a GitHub JSON, meeting notes (`.md`). Write them as people really write: short, messy, with typos.
3. Before anything is run, write the answer key in `gold.json`:
   - `facts`: an id mapped to a list of regular expressions. A fact counts as delivered if all its patterns match one line of the handover.
   - `conflicts`: the same, for each place two sources disagree. Plant at least 3.
   - `canaries`: confidential strings the successor must never see. Plant at least 5, some in a restricted source and some in a source the successor can read.
4. Write `manifest.json`, as in `foodlink/`, giving each source's audience and each person's clearances.

Then run:

```sh
python eval/evaluate_dataset.py eval/second_dataset/<name>
```

Report the score from the first run, and don't change the rules before reporting it.
