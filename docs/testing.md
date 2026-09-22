# Testing

Automated tests use fixed model observations and mocked provider responses to check code behavior. Live evaluation calls a real model through the HTTP API. Mocks are never a runtime chat mode.

## Automated tests

From the repository root, using Python 3.11 or newer:

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.lock
backend/.venv/bin/python -m pytest backend/tests -q
```

Build the frontend with Node.js 22:

```bash
cd frontend
npm ci
npm run build
```

The automated suite also covers disputed record ownership: revoke access, clear stale identity and drafts, reverify, replay safely, and roll back a failed reply.

## Local live evaluation

Configure the model in `.env` as described in the [README](../README.md#local-setup), then run from the repository root:

```bash
docker compose up --build -d
docker compose exec app python scripts/evaluate.py --output /tmp/live-evaluation.json
mkdir -p test-results
docker compose cp app:/tmp/live-evaluation.json test-results/live-evaluation.json
```

This uses real model requests and incurs provider charges. UI-only model settings are not passed to the evaluation script; use environment defaults.

## Cloud evaluation

With the Python environment above, test the sponsored deployment without sending a visitor key:

```bash
backend/.venv/bin/python scripts/evaluate.py   --base-url https://insurance-claims-sop-agent-d5gs.onrender.com   --server-model   --output test-results/cloud-evaluation.json
```

Replace the URL for another deployment. Omit `--server-model` to supply model settings from the local environment or `.env` on a local or visitor-key server. Add `--scenario chinese_consent` for a focused run; `--help` lists the scenarios.

## Coverage

The 16 scenarios cover:

- Three-field verification, alternate fields, invalid corrections, and bypass attempts.
- Remembered hints, case switching, ownership, and grounded payment information.
- Scope rejection, emotional recovery, refusal, and simulated human support.
- Summary review, conditional consent, invalid recipients, send/skip, and replay safety.
- Chinese replies, current-date deadlines, and response latency.

Most scenarios use `2026-03-10` to keep fixture deadlines reproducible. `current_date` uses today's date. Reports include checks, replies, workflow state, and timings; they exclude credentials, access tokens, and raw caller messages. Reports remain in the Git-ignored `test-results/` directory.

Review the replies as well as the counts: keyword checks do not prove factual or translation accuracy. Two concurrent sessions are a basic timing check, not a load test. See [latest results](evaluation-results.md).

For a smaller real-model smoke test, run `backend/.venv/bin/python scripts/smoke_test.py --base-url http://127.0.0.1:8000`. Add `--multilingual` to include Spanish and print replies for review. This script requires model credentials in the environment or `.env`.
