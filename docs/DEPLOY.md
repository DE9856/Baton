# Going live

Takes about 30 minutes, most of it the first image build on GitHub. Everything below reads from `.env`, which git ignores. Don't paste credentials into chat or commit them.

Copy `.env.example` to `.env` and fill it in as you go.

## 1. Supabase

1. Create a project at supabase.com and keep the database password somewhere safe. Use it for Baton only: Baton takes back Supabase's default rights on its own tables and expects `public` to be its own.
2. Click **Connect** at the top of the dashboard and choose **Session pooler**. Copy the URI, put your password in it, and set it as `BATON_DATABASE_URL`. If the password has symbols such as `@ / # :`, percent-encode them or choose a password without them.
   - Don't use the Direct connection: it's IPv6-only without a paid add-on, so it can fail from Azure or your network.
   - Don't use the Transaction pooler (port 6543).
   - The pooler's user looks like `postgres.abcdefgh`. That's still the `postgres` user.
3. **Project settings, API Keys:** copy the Project URL to `SUPABASE_URL`, and the publishable key (`sb_publishable_...`) to `SUPABASE_ANON_KEY`. An older project's legacy `anon` key works too. Never use the secret or `service_role` key here.
4. **Authentication, Sign In / Providers, Email: leave Confirm email on.** Baton lets a person into a project because their email address matches. With confirmation off, anyone could sign up using a reviewer's address and open that reviewer's projects.
5. **Optional, Project settings, Data API: turn it off.** Baton connects to the database directly and never uses this REST API, so turning it off removes something exposed to the internet.

Then check it. This creates the tables and policies, and tests what the database and a stranger with the public key can each reach:

```sh
python deploy/check_supabase.py
```

Then run the app and try it for real:

```sh
uvicorn baton.web.app:get_app --factory --port 8000
```

Load the demo, then sign up at `/signin` and confirm your email. To check that account from the script too, run `python deploy/check_supabase.py --account you@example.org`.

**Limits on Supabase's free plan:**

- **Sign-up email only reaches your Supabase team.** Without your own SMTP server, it refuses other addresses (`email_address_not_authorized`) and sends about 2 emails an hour. Invite teammates to the Supabase organisation, or add accounts by hand under **Authentication, Users**, with **Auto Confirm User** ticked.
- **Judges don't need accounts.** The demo and private sandboxes use the name-and-`pass` sign-in, and work with no email.
- **A project with too little activity for a week is paused.** Open the dashboard or the app before judging starts.

## 2. Groq

Create a key at console.groq.com and set `GROQ_API_KEY`. Check that `BATON_EXTRACT_MODEL` (default `openai/gpt-oss-120b`) is on Groq's current list. Uploads then use Groq; the demo keeps replaying its recorded run. To try it on the second dataset:

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

## 4. Azure Container Apps

Hugging Face Docker Spaces now need a paid PRO plan, so Baton runs on Azure Container Apps with Azure for Students credit (no card needed; sign up with a college email). The image is built by GitHub, so Azure needs no registry of its own.

1. Pushing to `main` or `round2-build` runs the **image** workflow (`.github/workflows/image.yml`), which publishes `ghcr.io/<owner>/baton`. Once, make the package public: GitHub profile, **Packages, baton, Package settings, Change visibility**. The image holds no credentials.
2. Install the Azure CLI and sign in. With uv, add pip so extensions can install:

   ```sh
   uv tool install azure-cli
   uv pip install --python ~/.local/share/uv/tools/azure-cli/bin/python pip
   az login --use-device-code
   az extension add --name containerapp
   az provider register --namespace Microsoft.App --wait
   az provider register --namespace Microsoft.OperationalInsights --wait
   ```

3. Create the group and environment. **Azure for Students only allows some regions**; list yours with `az policy assignment list`. Ours allows Austria East, UAE North, Malaysia West, Korea Central and East Asia. Austria East is the nearest to a Supabase project in eu-west-1.

   ```sh
   az group create --name baton-rg --location northeurope
   az containerapp env create --name baton-env --resource-group baton-rg --location austriaeast --logs-destination none
   ```

4. Deploy. This reads `.env`, sends the credentials as Azure secrets without printing them, and runs one always-on replica with 1 CPU and 2 GB. Jobs run in-process, so keep it to one replica. Run it again after each new image to roll out a new revision.

   ```sh
   python deploy/deploy_azure.py --dry-run
   python deploy/deploy_azure.py
   python eval/smoke_deployed.py https://<app>.<env-domain>.azurecontainerapps.io
   ```

The live link is `https://baton.happyground-10a16bb3.austriaeast.azurecontainerapps.io`. Logs: `az containerapp logs show -n baton -g baton-rg --follow`. **It sleeps when idle** (`--min-replicas 0`, set on 2 Nov 2026): it wakes on the first visit and sleeps again a few minutes after the last request. Measured on 2 Nov 2026: asleep about 2 minutes after the last request, awake in 7 seconds, and the deployed smoke test passed straight after (demo run 27 s). If Azure has to pull the image again, waking can take a minute or more. Data is in Supabase, so nothing is lost. Asleep, it stays within Azure's monthly free allowance. Always on costs roughly US$25 to 75 a month (about ₹2,000 to 6,000) against ₹9,523 of student credit, so if you want no wait at all during judging, turn it on just for then:

```sh
az containerapp update -n baton -g baton-rg --min-replicas 1   # before judging
az containerapp update -n baton -g baton-rg --min-replicas 0   # after
```

The deploy script creates the app always on; set it back to sleep after a first deploy.

`deploy/deploy_space.py` (Hugging Face) is kept for anyone with a PRO account.

## If something goes wrong

| Symptom | Likely cause |
|---|---|
| App log says "password authentication failed" | Wrong password in `BATON_DATABASE_URL` |
| Connection refused, "Network is unreachable" or a host that can't be found | You're on the direct connection. Use the Session pooler string |
| "Tenant or user not found" | The pooler's user must be `postgres.<project-ref>`, and the region in the host must be right. Copy the whole string from **Connect** |
| Pages are slow on Supabase | The app and database are far apart. `deploy/check_supabase.py` prints the round trip. Put them in the same region |
| "Permission denied to set role authenticated" | The URL's user isn't `postgres`. Use the `postgres` user |
| "Email address not authorized" on sign-up | Supabase's built-in email only reaches your team. See the limits above |
| Can't connect after a quiet week | The free project was paused. Restore it in the dashboard |
| "The Groq API key was rejected" in a run | Check `GROQ_API_KEY` in `.env`, then run `deploy/deploy_azure.py` again |
| "Groq doesn't offer the model" | Change `BATON_EXTRACT_MODEL` to one listed at console.groq.com/docs/models |
| `RequestDisallowedByAzure` | The region isn't allowed on your subscription. Use one from `az policy assignment list` |
