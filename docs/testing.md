# Testing

The automated tests check SOP rules with injected model observations and mock HTTP responses. The live evaluation calls the configured model through the running application's HTTP API. Mocks are never a runtime chat mode.

## Run the live evaluation

Configure `MODEL_API_PROTOCOL`, `MODEL_BASE_URL`, `MODEL_NAME`, and `MODEL_API_KEY` in the project `.env`, then start the local app:

```bash
docker compose up --build --force-recreate -d
docker compose exec app python scripts/evaluate.py --output /tmp/live-evaluation.json
mkdir -p test-results
docker compose cp app:/tmp/live-evaluation.json test-results/live-evaluation.json
```

This uses real model requests and incurs provider charges. Only the supplied synthetic customer fixtures are used. The report contains checks, assistant replies, selected workflow state, and latency; credentials, session access tokens, and raw caller messages are excluded. `test-results/` is ignored by Git.

To evaluate another running instance, use the project's Python environment:

```bash
backend/.venv/bin/python scripts/evaluate.py \
  --base-url http://127.0.0.1:8001 \
  --output test-results/hosted-evaluation.json
```

The same command accepts your deployed HTTPS URL. The client supplies the model configuration for each session, which also works with hosted mode. To repeat one scenario, add `--scenario chinese_consent` or another scenario name from `--help`.

## Coverage and limits

- Three matching identity fields, alternate fields, incorrect inputs, and attempted gate bypass.
- Remembered case hints, grounded follow-ups, case corrections, and case ownership.
- Recorded payment amounts, unsupported premium questions, and expired appeal deadlines.
- Emotional replies, repeated verification refusal, scope limits, and simulated human handoff.
- Optional summary review, conditional consent, explicit send/skip, and idempotent retries.
- Chinese conversation continuity after an English UI button action.
- End-to-end message latency and a small comparison of sequential versus two concurrent sessions.

Live scenarios use the historical business date `2026-03-10` for reproducible fixture deadlines, except `current_date`, which checks the deadline using today's date. Each scenario records its date in the report. Normal conversations use today's date. Email delivery and human handoff are simulated. Two concurrent sessions are a basic performance check, not a load test. Keyword and language assertions are heuristics, so inspect the saved replies as well; passing one run does not guarantee every future model response.

For deterministic regression tests in an installed development environment:

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```
