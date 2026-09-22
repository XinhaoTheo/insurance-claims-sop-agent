# Evaluation results

Tested September 22, 2026 with `gpt-5.4-mini` through the official OpenAI API. Customer data is synthetic. The same Docker image serves local and hosted modes.

## Local verification

- Automated regression tests: **224 passed**, plus **26 subtests**. Two upstream deprecation warnings remain.
- Frontend production build and Docker image build: passed.
- Full hosted-mode preview: **206 checks passed**, no failed assertions across 39 successful conversation turns, two replays, and four benchmark turns. One additional turn returned HTTP 502.
- The failed current-date scenario passed its focused rerun: **7/7 checks**, 2.402 seconds. The original report retained the HTTP status but not the error detail, so its exact cause is unknown.
- Separate local-mode payment/language regression: **12/12 checks**.

All 13 scenarios were exercised, covering identity gates, retained case hints, case ownership and correction, grounding, scope rejection, emotional recovery, human handoff, email consent, draft review, multilingual replies, and idempotent retries. Review of the saved successful replies found no additional material workflow or factual failure. Minor wording can still be improved, including literal Chinese translations of clinical terminology.

| Measurement | Local hosted-mode preview |
| --- | --- |
| Successful conversation latency, median | 2.457 s |
| Successful conversation latency, p95 | 4.589 s |
| Successful conversation latency, maximum | 5.030 s |
| Replay latency, median | 0.005 s |
| Two sequential requests, total | 5.051 s |
| Two concurrent requests, total | 2.825 s |
| Idle container memory snapshot | About 38–41 MiB |

Latency excludes the failed request; this is not an availability guarantee. These measurements were taken on local Docker and do not measure Render's cold start or internet latency. Two concurrent sessions are a basic check, not a load test.

## Render deployment

The [public demo](https://insurance-claims-sop-agent-d5gs.onrender.com) runs on Render Free from `feat/hosted-demo`. HTTPS, the chat UI, hosted model defaults, and `/health` were checked. Visitors supply their own API key; no shared model key was configured on Render. Idle sleep and temporary SQLite storage are intentional demo limits.

The first public run at `619115b` completed 40 conversation turns with no HTTP errors and **212/213 checks passed**. It exposed an intermittent omitted claim-status answer during email consent. A passing retry did not erase that defect. The prompt was corrected in `ad49206` to preserve explicit claim questions during wrap-up, and coverage was expanded to three distinct phrasings.

The final public run at **`ad49206` passed 225/225 checks** across **13 scenarios**, **42 conversation turns**, **2 replays**, and **4 benchmark turns**, with **no failed assertions or HTTP/model errors**. All three post-process phrasings answered the claim question while preserving the optional email choice. The matching local regression passed 27/27 checks.

| Measurement | Render Free, warm service |
| --- | --- |
| Conversation latency, median | 2.600 s |
| Conversation latency, p95 | 3.285 s |
| Conversation latency, maximum | 6.685 s |
| Replay latency, median | 0.157 s |
| Two sequential requests, total | 5.334 s |
| Two concurrent requests, total | 2.762 s |

These end-to-end times include network transit and both model calls. Idle wake-up time was not benchmarked. Results describe this run, not a guarantee of future model behavior or service availability. The original failures remain recorded separately from the corrected final run.

## Reproduce

Follow [Testing](testing.md). Raw reports stay in the ignored `test-results/` directory. Language and keyword assertions are heuristics, so review replies as well as the numeric result. Model responses can vary between runs.

Email delivery and human transfers are simulated. No real email, claim decision, appeal, payment, or human connection is performed. Anthropic transport is covered by mocked HTTP tests; this evaluation used a real OpenAI key and did not test a live Anthropic account.
