# Privacy, security, accessibility and responsible design

This says what the product does today and where it falls short. Claims here are backed by tests unless marked otherwise.

## Privacy

| Promise | How it's kept |
|---|---|
| Only authorised sources | Each source records who authorised it, its audience and a consent note. An upload needs a tick-box confirmation. No scraping and no admin tokens |
| Raw exports aren't kept | Uploads are parsed and discarded; only parsed records stay. A GitHub token is used once and never stored |
| Secrets aren't stored | Recognised credentials are masked before storage or any AI call. A test searches every table for the planted secrets |
| Hosted AI sees as little as possible | One chunk of one source per call, with credentials masked. Personal details in a chunk **are** sent if Groq is configured. Without a key, nothing leaves the server |
| Speech stays on the server | Whisper runs locally. If a transcript contains a credential, the audio is deleted |
| No surveillance | Runs per handover, for one project, visible to the leaver, who can read and dispute what's said about them. No productivity scores |

**Not yet done:** a way to delete a project and its data. There is no automatic retention limit, so demo and sandbox data stays until the database is cleared.

## Security

| Layer | Detail |
|---|---|
| Who can open a project | Demo: anyone (synthetic data only, and it refuses uploads). Sandbox: only the browser that made it. Member project: only signed-in people whose email the project lists. Others get the same 404 as a project that doesn't exist |
| What a person can read | `audience in clearances AND (reviewer OR not sensitive)`. On Postgres the database decides under row-level security with the viewer's identity, then a SQL filter and a Python check apply. A test breaks the app's checks and confirms the database still refuses |
| Immutability | Approved versions and audit events can't be changed or deleted: an app-level guard, plus a database trigger on Postgres |
| Prompt injection | The extractor has no tools and sees one chunk. Output must fit a schema, and each claim must quote its record word for word, so injected text can at most appear as a visible, sourced quote. **Not tested against a real model yet** |
| Web | Templates escape all source text (tested), cookies are signed and `SameSite=Lax`, redirects stay on-site, uploads are size-limited, audio types are allow-listed and served with `nosniff` |
| Audit | Connecting, viewing, editing, approving, exporting, asking and playing audio are all logged |

**Known weaknesses:**

- The "Viewing as" picker has no password. That's deliberate on the synthetic demo and on single-browser sandboxes, and it isn't available on member projects.
- The sensitive guard is regex-based. It missed a password on its first run on new data (fixed), and it will miss others. The upload screen says so.
- Sandboxes rely on the signed cookie. Clearing cookies loses access.
- No rate limiting on sign-in beyond Supabase's own, and only a simple cap on concurrent pipeline runs.
- The app's database connection is the table owner: reviewer actions are checked in app code, and RLS protects reads rather than writes through the app. Direct writes by the `authenticated` role are refused.
- No independent security review has been done.

## Accessibility

- **Checked:** axe-core with WCAG 2.1 A and AA rules runs in the browser test on the home, connect, interview, review, handover and Ask pages. No serious or critical violations remain. It caught a real contrast failure, now fixed.
- **By design:** every label is text, never colour alone. Pages are server-rendered and work without JavaScript, except live progress and in-browser recording, which have fallbacks. Form controls have labels. The page doesn't scroll sideways at phone width.
- **Not done:** a test with a screen reader or keyboard-only user. Automated checks catch only part of what matters, so this isn't a claim of WCAG conformance.

## Responsible design

- **Assumptions aren't shown as fact.** The successor sees Verified, Confirmed by person or Missing. Verified needs a code-checked quote, a support check and all values present.
- **Humans decide.** Nothing is shared until a named reviewer approves. Conflicts are never auto-resolved. A claim a reviewer accepts after a failed check becomes their word, not Verified.
- **The AI doesn't judge itself.** The extractor proposes; code and a separate classifier check; a person approves.
- **It says what it doesn't know.** Ask Baton abstains. Failed AI calls mark a run incomplete. If the checking model is down, nothing is labelled Verified.
- **It can be wrong.** Each handover says "Baton may miss things", and our testing shows real limits ([TESTING.md](TESTING.md)).
