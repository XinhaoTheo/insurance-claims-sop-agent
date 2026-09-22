# Hosting

**[Live demo](https://insurance-claims-sop-agent-d5gs.onrender.com)** · Render Free · branch `feat/hosted-demo`

The cloud and local versions use the same Dockerfile, UI, API, and SOP. Run one instance with one worker. SQLite is created at `/data/insurance.db`; no separate database service is needed.

## Deploy on Render

1. Push the application and root `render.yaml` to GitHub.
2. In Render, choose **New → Blueprint**, connect the repository, and select `feat/hosted-demo`.
3. Review the Blueprint: one Docker web service on the Free plan, no disk or paid database, health check `/health`.
4. Add `MODEL_API_KEY` in the service's **Environment** settings. The Blueprint supplies `openai`, the official endpoint, and `gpt-5.4-mini`; change these if needed.
5. Save and deploy. Open the generated HTTPS URL once the deployment is live.

Keep keys in Render environment settings, never in Git or `render.yaml`. Model usage is billed to the key owner. With the server key configured, visitors can chat immediately and model settings are hidden.

Subsequent pushes to the connected branch trigger deployments. Check **Deploys**, **Logs**, and `/health` when diagnosing startup failures.

## Storage and model settings

| Setting | Behavior |
| --- | --- |
| `HOSTED_DEMO=true` | Enable hosted endpoint restrictions and the shared limit of 60 API POST requests per minute. |
| `MODEL_API_KEY` | Fund visitor chats with the server model. If absent, visitors configure their own credentials. |
| `MODEL_API_PROTOCOL`, `MODEL_NAME`, `MODEL_BASE_URL` | Select the protocol, model, and API root. A blank URL uses the protocol's official endpoint. |
| `HOSTED_MODEL_BASE_URLS` | Optional comma-separated endpoint allowlist. Defaults to the official OpenAI and Anthropic roots. |
| `DATABASE_PATH=/data/insurance.db` | SQLite location. |
| `PORT` | HTTP port used by the container entrypoint. |

Visitor keys stay in backend memory and expire after one hour; a restart requires reconnection. Server-configured keys remain available. Hosted mode with a server key rejects visitor model overrides.

Render Free storage is temporary: conversations may disappear after sleep, restart, or deployment. This is intentional for the demo. An idle service may take about a minute to wake. For durable conversations, use a paid service with a disk mounted at `/data`; check [Render's current plans](https://render.com/pricing) before upgrading.

## Optional: Railway

Deploy the same Dockerfile with one replica, health check `/health`, and a public domain targeting port `8000`. Set the model variables above, `HOSTED_DEMO=true`, `PORT=8000`, and `DATABASE_PATH=/data/insurance.db`.

For persistence, mount a volume at `/data` and set `RAILWAY_RUN_UID=0`. The entrypoint prepares volume permissions, then drops to the application user. Keep the image's startup command. Check [Railway's plans](https://railway.com/pricing) before deploying.

Use [the live evaluation](testing.md#cloud-evaluation) to verify a deployment. Email and human transfers remain simulated on all hosts.
