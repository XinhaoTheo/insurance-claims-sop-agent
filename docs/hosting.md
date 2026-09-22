# Hosted demo

The hosted demo uses the same Docker image, React UI, FastAPI backend, and SOP workflow as the local version. Use one service instance and one worker. SQLite lives at `/data/insurance.db`; no separate database service is required.

Select `feat/hosted-demo` while hosted support is under development; use `main` after it is merged. Deployment begins when you create a service in your own hosting account.

## Choose a host

Prices checked on September 22, 2026. Model API charges are separate.

| Option | Hosting cost | Storage and availability |
| --- | --- | --- |
| **Railway Hobby — recommended** | **$5/month minimum**, including $5 of resource usage; extra usage is billed | Attach a persistent volume for SQLite. Keep Serverless disabled for an immediately available interview demo. |
| Render Free | $0 within free usage limits | No persistent disk. Sleeps after 15 idle minutes; waking takes about one minute. Conversations disappear on sleep, restart, or redeploy. |
| Render paid | $7/month compute + $0.25/month for a 1 GB disk = **$7.25/month** before extras | Persistent SQLite and no free-tier idle sleep. Current compute plan ID: `0.5c-512mb`. |

Railway's $5 is a minimum bill, not a fixed maximum. For example, $3 of monthly resource usage costs $5; $8 costs $8. Set a usage alert and, if desired, a $10 hard compute limit, the current minimum. Reaching that limit takes the service offline. [Railway pricing](https://railway.com/pricing), [cost controls](https://docs.railway.com/pricing/cost-control).

Render Free is useful for a first public preview if losing demo conversations is acceptable. Free Postgres is not needed and would expire after 30 days. [Render pricing](https://render.com/pricing), [free service limits](https://render.com/docs/free), [persistent disks](https://render.com/docs/disks).

## Railway: durable public demo

1. In Railway, create a project from the GitHub repository `XinhaoTheo/insurance-claims-sop-agent`. Select the pushed `feat/hosted-demo` branch. Railway builds the repository's Dockerfile.
2. Add these service variables:

   ```dotenv
   HOSTED_DEMO=true
   PORT=8000
   DATABASE_PATH=/data/insurance.db
   RAILWAY_RUN_UID=0
   ```

   Railway mounts volumes as root. `RAILWAY_RUN_UID=0` lets the entrypoint prepare `/data`, then drop to the application's non-root user before starting FastAPI. Do not override the image's startup command.

3. Add a volume attached to this service and set its mount path to `/data`. The directory created while building the image is not a substitute for a mounted volume.
4. In deployment settings, keep **one replica**, set the healthcheck path to `/health`, and leave **Serverless disabled**. Deploy the service.
5. In **Settings → Networking → Public Networking**, choose **Generate Domain** and use target port `8000`. Railway provides the public domain and HTTPS certificate.
6. Open the generated URL. In **Model settings**, select a protocol, enter a valid model name and your own API key, then test and apply the settings. Start a conversation with the Margaret Chen example from the README.

A volume preserves SQLite across deployments, but a redeploy briefly stops the service and clears in-memory model credentials. Configure the model again after a restart. Optional Serverless sleeping reduces resource usage, but introduces a cold start and may return a 502 on the first wake request; it also clears temporary credentials.

References: [Docker builds](https://docs.railway.com/builds/dockerfiles), [volume permissions](https://docs.railway.com/volumes#permissions), [healthchecks and ports](https://docs.railway.com/deployments/healthchecks), [public domains](https://docs.railway.com/networking/public-networking), [Serverless behavior](https://docs.railway.com/deployments/serverless).

## Render: free public preview

1. Open the GitHub repository and confirm that the selected branch contains `render.yaml`.
2. In Render, choose **New → Blueprint**, connect the repository, select `feat/hosted-demo`, and use the root `render.yaml` file.
3. Review the configuration: one Docker web service on the **Free** compute plan, with no paid database or disk. Create the Blueprint.
4. Wait for the deployment to become healthy, then open its generated `onrender.com` URL. Configure your model in the page as described above.

The Blueprint enables `HOSTED_DEMO=true` and checks `/health`. Render provides HTTPS. After a free instance sleeps or restarts, its SQLite database is recreated. If the browser tries to reopen a conversation that no longer exists, the UI recovers by starting a new one; enter your model settings again.

To keep conversations, switch to the paid `0.5c-512mb` plan and add a 1 GB disk mounted at `/data`. This changes the hosting cost to approximately $7.25/month before extras. [Blueprint configuration](https://render.com/docs/blueprint-spec), [disk setup](https://render.com/docs/disks).

## Model settings and public access

- Hosted mode requires each visitor's own API key. A server `MODEL_API_KEY` is ignored in hosted mode, so publishing the URL does not expose a shared model budget.
- Keys travel over HTTPS to the demo backend and are used there to call the selected provider. They are stored only in server memory, and the model connection expires after one hour. Expired keys are removed on a subsequent API request; disconnect, completed handoff/conversation, or restart also clears them. Keys are not written to SQLite.
- The default allowed endpoints are `https://api.openai.com/v1` and `https://api.anthropic.com/v1`. An administrator can change the comma-separated `HOSTED_MODEL_BASE_URLS` environment variable to allow another trusted endpoint. Visitors cannot make the public backend call arbitrary URLs.
- Hosted mode limits POST requests to 60 per minute across the whole instance. Local Docker remains configurable without this hosted limit.
- Use the supplied synthetic customer data when trying the public demo. Email delivery and human transfers are simulated.

For automated evaluation, use the same HTTP API as the UI and supply model settings when creating or configuring a session. The local Docker setup remains available for evaluators who prefer to run the application with their own infrastructure.
