# Evaluation results

Tested September 22, 2026 with `gpt-5.4-mini` through the official OpenAI API. Customer data is synthetic. The same Docker image serves local and hosted modes.

## Latest cloud regression: `3a8c104`

The operator-funded Render deployment passed **275/275 checks** across **16 scenarios**, **53 conversation turns**, **3 idempotent replays**, and **4 benchmark turns**, with **no failed assertions or HTTP/model errors**. Visitors supplied no API keys. The deployed frontend and public model configuration were also checked.

Coverage includes the complete send/skip workflow, identity corrections, unresolved fourth identity fields, alternate verification after SSN refusal, remembered case hints, ownership, grounded payments, scope limits, emotional recovery, and Chinese conditional consent. Unusable recipient addresses block sending; references to the registered address permit explicit consent. Reviewing the saved replies found no additional material SOP or factual error. Chinese medical-document translations still have minor wording roughness.

| Measurement | Render Free, warm service |
| --- | --- |
| Conversation latency, median | 2.580 s |
| Conversation latency, p95 | 3.938 s |
| Conversation latency, maximum | 4.408 s |
| Replay latency, median | 0.156 s |
| Two sequential requests, total | 6.228 s |
| Two concurrent requests, total | 3.010 s |

The matching local suite passed **260 tests and 22 subtests**. Frontend production build passed, and Render built and deployed the Docker application successfully.

Earlier attempts remain recorded. `728bbf1` had 11 failed assertions and one HTTP 502: registered-email references were misclassified, an incomplete address failed schema validation, and a claim identifier was confused with a policy identifier. `9d37871` fixed those cases but had 11 failed assertions from an SSN-refusal interpretation and a conditional email request being treated as a final skip. Prompt and schema descriptions were clarified in `3a8c104`; the backend did not add language-specific parsing. Raw reports are `test-results/render-728bbf1.json`, `render-9d37871.json`, and `render-3a8c104.json`.

These measurements cover this run, not cold starts or sustained load. Model behavior can vary; passing one run is not a guarantee for every future conversation.

## Earlier local verification

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

## Earlier Render deployment

The [public demo](https://insurance-claims-sop-agent-d5gs.onrender.com) runs on Render Free from `feat/hosted-demo`. HTTPS, the chat UI, hosted model defaults, and `/health` were checked. These original measurements used visitor-supplied keys. The deployment now supports an operator-funded server key so visitors can chat without configuration. Idle sleep and temporary SQLite storage are intentional demo limits.

The first public run at `619115b` completed 40 conversation turns with no HTTP errors and **212/213 checks passed**. It exposed an intermittent omitted claim-status answer during email consent. A passing retry did not erase that defect. The prompt was corrected in `ad49206` to preserve explicit claim questions during wrap-up, and coverage was expanded to three distinct phrasings.

The earlier full public run at **`ad49206` passed 225/225 checks** across **13 scenarios**, **42 conversation turns**, **2 replays**, and **4 benchmark turns**, with **no failed assertions or HTTP/model errors**. All three post-process phrasings answered the claim question while preserving the optional email choice. The matching local regression passed 27/27 checks.

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
