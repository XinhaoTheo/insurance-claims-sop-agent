# Insurance Claims SOP Agent

A local web demo of a conversational claims-support agent whose business permissions are controlled by code. A model interprets language; a deterministic harness enforces identity verification, case ownership, phase order, grounded answers, and optional email consent.

```text
VERIFY_ID → RESOLVE_INTENT → PROCESS_CASE → POST_PROCESS
```

**Delivery:** source repository, Docker build recipe, local chat UI, and a documented HTTP API for automated evaluation. There is no hosted deployment or published registry image in this version. The Docker image is built locally from this repository.

## Docker quickstart

Install Docker Desktop (or Docker Engine with Compose), then run:

```bash
git clone https://github.com/XinhaoTheo/insurance-claims-sop-agent.git
cd insurance-claims-sop-agent
cp .env.example .env
docker compose up --build -d
```

Open **[http://localhost:8000](http://localhost:8000)** in your browser. The image includes the web UI, SOP backend, test fixtures, and automatic SQLite initialization. No separate Python, Node.js, or database installation is required.

1. Start in **Offline fixture** mode to try the fixed examples and workflow without an API key.
2. Click **Model settings** and select **Live AI model**.
3. Enter the **Base URL**, **Model name**, and **API key** for an OpenAI-compatible endpoint.
4. Click **Test connection**. Once it succeeds, click **Use live model**.
5. Chat with the agent and inspect the current phase, remembered context, execution events, and email summary.

**Offline mode uses deterministic parsing and does not call AI. Use Live mode to evaluate natural-language understanding.** In Live mode, the local application contacts your selected model provider and sends relevant messages and limited conversation context to that provider.

```bash
# Read startup logs and health
docker compose logs -f app
docker compose ps

# Stop the service; keep the database volume
docker compose down

# Rebuild after pulling a code update
docker compose up --build -d
```

The `claims-data` volume stores SQLite data and persists across normal stops, starts, and container replacements. Use `docker compose down -v` only for a complete reset: it deletes the volume and its demo records.

The service binds to `127.0.0.1:8000`, so it is intended for the local machine. One application process is intentional: temporary model credentials and identity values live in process memory.

## What the demo shows

| Capability | Mechanism |
| --- | --- |
| Strict identity gate | At least three distinct allowed PII categories must match the same unique customer. Policy number is a lookup hint, not a fourth identity category. |
| Memory across phases | A caller can mention a denied January healthcare claim during verification. Its hints and source turn are retained, while claim queries remain blocked. |
| Bounded model reasoning | The live model returns validated identity observations, hints, intent, topic, emotional signals, and choices. It cannot return an authoritative phase or verification flag. |
| Grounded claim support | Authorized tools select a customer's claim and relevant fixture guidance. Status, amounts, dates, and outcomes are rendered deterministically; the model does not invent free-form business facts. |
| Natural variations and emotions | Live interpretation handles partial answers, clarification, refusal, emotions, and mixed-topic messages. The harness provides empathy, alternatives, and bounded recovery. |
| Scope control | Unrelated questions are declined. Useful insurance details in mixed messages are still saved. Repeated unrelated questions offer a simulated handoff. |
| Explicit follow-up choice | A versioned summary is prepared; sending requires an affirmative choice, or the customer can skip. No real email is sent. |
| Inspectable execution | The UI and API expose phase changes, tool events, grounding sources, and redacted memory, rather than model chain of thought. |

### Try the supplied example

```text
I’m the policyholder. My name is Margaret Chen, policy POL-9921.
I’m calling about my denied healthcare claim from January.
DOB is 1985-03-15, SSN last four is 4472.
```

Expected: verify the three supplied PII categories, reuse the remembered case hints, select `CL-2048`, then explain its recorded denial and next steps. All phase transitions occur in order, even if several happen inside one turn.

Continue with:

```text
What documents do I need?
How do I submit them?
That's all, no more questions.
Yes, send me the summary.
```

In a fresh session, choose `No thanks, skip the email.` at the final step to exercise the other branch.

**Business date matters.** By default each session uses the actual current date. The supplied appeal deadline is `2026-03-18`, so it may already be expired. To explicitly test a historical pre-deadline scenario, set `DEMO_DATE=2026-03-10` before starting the server, or pass `"demo_date":"2026-03-10"` when creating an API session. The UI displays the active business date; no clock is silently backdated.

## Model configuration

Supported providers expose the OpenAI-compatible **Chat Completions** endpoint with JSON-object output:

```text
POST {MODEL_BASE_URL}/chat/completions
Authorization: Bearer <your model API key>
response_format: {"type": "json_object"}
```

Use a base URL such as `https://api.openai.com/v1`, **not** the full `/chat/completions` URL. Choose a model name that your provider supports. Compatibility with arbitrary providers is not guaranteed; the connection test makes a real request to confirm authentication and JSON output, and may incur a small model charge.

You can configure a session in the UI, or set optional server defaults in `.env`:

```dotenv
MODEL_API_KEY=your-own-key
MODEL_BASE_URL=https://api.openai.com/v1
MODEL_NAME=your-supported-model
DEMO_DATE=
SIMULATE_EMAIL_FAILURE=false
```

- Session-supplied keys are held temporarily in backend memory, never in SQLite or browser storage. They expire after one hour and are cleared when the session completes, hands off, explicitly disconnects, or the server restarts.
- Deployment defaults supplied in `.env` remain in that file and the server environment until you remove them. Do not commit `.env` or embed keys in an image.
- HTTPS is required for remote model endpoints. Loopback HTTP (`localhost`, `127.0.0.1`, `::1`) is accepted for a directly running local model server. Inside Docker, loopback refers to the container; it does not automatically reach a model running on the host.
- A custom endpoint requires its own caller-supplied key; the server will not forward its default key to a caller-selected address.
- Model request failure leaves the workflow unadvanced; the app does not silently switch a live session into offline mode.

Switching to Offline fixture clears that session's temporary model credentials. Continuing an active live session after a server restart requires reconnecting the model. Verified access in an active session expires after one hour and requires verification again; completed sessions remain closed.

## API for AI-driven evaluation

The UI uses the same API exposed to automated callers. Interactive documentation is available at **[http://localhost:8000/docs](http://localhost:8000/docs)** and the schema at `/openapi.json`.

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Process health and explicit mock email mode |
| `GET /api/config` | Nonsecret defaults |
| `POST /api/models/test` | Actual model connection check, or explicit offline status |
| `POST /api/sessions` | Create session; returns `session_id` and `access_token` |
| `GET /api/sessions/{id}` | Read redacted snapshot |
| `POST /api/sessions/{id}/messages` | Submit `{message, turn_id}` |
| `GET /api/sessions/{id}/trace` | Read events and public state |
| `POST /api/sessions/{id}/model` | Connect or change that session's model |
| `DELETE /api/sessions/{id}/model` | Clear its key and explicitly switch to offline mode |

All endpoints under `/api/sessions/{id}` require `Authorization: Bearer <access_token>`. This generated **session access token is different from the model API key**. It grants access only to that session. Creating a session is available locally without an account; this is not a public production authentication system.

Create an offline session:

```bash
curl -sS http://localhost:8000/api/sessions \
  -H 'Content-Type: application/json' \
  -d '{"mode":"offline","demo_date":"2026-03-10"}'
```

Use the returned identifiers, without publishing them:

```bash
curl -sS "http://localhost:8000/api/sessions/SESSION_ID/messages" \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer SESSION_ACCESS_TOKEN' \
  -d '{"turn_id":"evaluation-001","message":"My name is Margaret Chen. DOB is 1985-03-15. SSN last four is 4472. I am calling about my denied healthcare claim from January."}'
```

For live evaluation, create a session with `mode: "live"` and `api_key`, `base_url`, `model`, or omit those three when server defaults are configured. Keep credentials out of committed test files, transcripts, and shell history.

`turn_id` is required and unique per logical message within a session. Retrying the same ID with the same message returns the original reply with the current session snapshot, without repeating business actions or reverting the UI to an older phase. Reusing it for different text returns HTTP 409. Calls in one session are serialized. HTTP 401 indicates a missing access token; HTTP 404 hides whether a foreign session exists; HTTP 502 indicates a model call, analysis-format, or tool failure.

Run the included stdlib-only HTTP smoke test against an already started server:

```bash
python3 scripts/smoke_test.py
# Or from the running application container:
docker compose exec app python scripts/smoke_test.py
```

It creates synthetic sessions and checks verification, remembered hints, grounded explanation, send/skip, idempotency, session isolation, failed verification, and repeated off-topic handling. It deliberately uses offline mode and an explicit historical date; it does not evaluate live model quality.

## Tests and development without Docker

Prerequisites: Python 3.12 and Node.js 22 with npm. Backend dependencies are pinned in `backend/requirements.lock`; frontend dependencies use `frontend/package-lock.json`.

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.lock
npm --prefix frontend ci
npm --prefix frontend run build
backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Then open `http://localhost:8000`. In a separate terminal:

```bash
cd backend
.venv/bin/python -m pytest -q
```

Inside Docker, run tests with:

```bash
docker compose exec -w /app/backend app python -m pytest -q
```

For frontend development, run `npm --prefix frontend run dev` with the backend running. Vite proxies `/api` requests to the backend. A production-like build is served directly by FastAPI.

## Data and boundaries

- The six supplied JSON files are **synthetic, read-only demo fixtures**. Identity verification uses their records; it is not a production identity-assurance system.
- SQLite stores state snapshots, redacted conversations, event records, idempotency receipts, and the simulated outbox. Parsed raw verification fields are held temporarily in memory. Recognized identity values are redacted before transcript persistence; this is not a general-purpose PII detection guarantee for arbitrary text.
- An identity correction revokes protected access. Every protected claim lookup rechecks verified state and ownership.
- Stored case hints are caller statements, not verified business facts. The dataset contains creation dates, not treatment dates; the agent asks for a claim number or creation date when needed.
- `representatives.json` and `consent_scenarios.json` are reserved fixtures. This version does **not** implement representative authorization; relationship information alone never grants access and the agent offers simulated human support.
- Email, human handoff, document upload, and appeal submission are not integrated external services. Email consent produces a local simulated outbox record; no actual email is sent. No claim decision is changed.
- The initial implementation uses single-process, local-only deployment. Public hosting would require deployment authentication, abuse limits, endpoint restrictions, retention controls, operational monitoring, and a deliberate secrets/session-storage design. Do not simply expose this local service to the internet.

See [the architecture and workflow diagrams](docs/architecture.md) for module responsibilities and the trust boundaries.

## Repository structure

```text
backend/app/          FastAPI, deterministic harness, tools, model adapter, SQLite
backend/tests/        Policy, API, storage, and model adapter tests
frontend/             React + TypeScript chat and workflow inspector
fixtures/             Supplied synthetic customers, claims, and guidance
scripts/smoke_test.py  Dependency-free HTTP evaluation client
docs/architecture.md  Design, phase diagrams, memory and consent mechanisms
Dockerfile            Node frontend build + Python application image
compose.yaml          Local-only port and persistent SQLite volume
.env.example          Optional model defaults and test-date configuration
```
