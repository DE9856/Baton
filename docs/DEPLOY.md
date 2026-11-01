# Going live

Takes about 30 minutes, most of it the first image build on Hugging Face. Everything below reads from `.env`, which git ignores. Don't paste credentials into chat or commit them.

Copy `.env.example` to `.env` and fill it in as you go.

## 1. Supabase

1. Create a project at supabase.com.
2. **Project settings, Database, Connection string:** copy the URI for the `postgres` user, put your password in it, and set it as `BATON_DATABASE_URL`.
3. **Project settings, API:** copy the Project URL to `SUPABASE_URL` and the `anon` key to `SUPABASE_ANON_KEY`.
4. **Authentication, Sign In / Providers, Email:** decide whether to require email confirmation. Turning it off is easier for judges.

On first start, Baton creates its tables and applies the row-level security policies in `baton/rls.sql`. Check locally first:

```sh
uvicorn baton.web.app:get_app --factory --port 8000
```

Then load the demo and sign up at `/signin`.

## 2. Groq

Create a key at console.groq.com and set `GROQ_API_KEY`. Check that `BATON_EXTRACT_MODEL` is on Groq's current list. Uploads then use Groq; the demo keeps replaying its recorded run. To try it on the second dataset:

```sh
python eval/evaluate_dataset.py eval/second_dataset/foodlink
```

Groq receives one chunk of one source per call, with recognised credentials already masked.

## 3. GitHub

Create an empty repository and a token that can push to it. Set `GITHUB_TOKEN` and `GITHUB_REPO` (owner/name). Then:

```sh
python deploy/publish_github.py --dry-run
python deploy/publish_github.py
```

This pushes to `main` and the current branch, then waits for CI. The brief needs the repository to be public or judge-accessible.

## 4. Hugging Face

Create a token with write access. Set `HF_TOKEN` and `HF_SPACE` (owner/name). Then:

```sh
python deploy/deploy_space.py --dry-run
python deploy/deploy_space.py
```

The first build takes several minutes, because it bakes in the three models. When it finishes, test the live link:

```sh
python eval/smoke_deployed.py https://owner-name.hf.space
```

Give judges the direct `https://owner-name.hf.space` link rather than the huggingface.co page: the page embeds the app in a frame, and browsers may block the sign-in cookie there. A free Space sleeps when idle, so open it a few minutes before judging. Railway is the plan's paid fallback, and it runs the same Dockerfile.

## If something goes wrong

| Symptom | Likely cause |
|---|---|
| Space log says "password authentication failed" | Wrong password in `BATON_DATABASE_URL` |
| Connection refused or very slow on Supabase | Try the pooler connection string (session mode) |
| "Permission denied to set role authenticated" | The URL's user isn't `postgres`, or is a transaction-pooler user. Use the `postgres` user |
| "The Groq API key was rejected" in a run | Check `GROQ_API_KEY` in the Space's secrets |
| "Groq doesn't offer the model" | Change `BATON_EXTRACT_MODEL` |
| Sign-in works but the session is lost on every page | Open the direct link, not the embedded page |
