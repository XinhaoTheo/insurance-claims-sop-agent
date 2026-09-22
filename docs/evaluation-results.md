# Evaluation results

Tested **September 22, 2026**, application commit **`3a8c104`**, on [Render Free](https://insurance-claims-sop-agent-d5gs.onrender.com) using **`gpt-5.4-mini`** through the official OpenAI API. The server funded model access; visitors supplied no keys.

## Latest results

| Check | Result |
| --- | --- |
| Cloud evaluation | **275/275 checks passed**, no HTTP/model errors |
| Coverage | 16 scenarios, 53 conversation turns, 3 replays, 4 benchmark turns |
| Local regression | 260 tests and 22 subtests passed |
| Frontend build | Passed |
| Render Docker build and deployment | Passed |

Coverage includes verification and corrections, SSN alternatives, remembered hints, case ownership, grounded payments, scope, emotional recovery, Chinese consent, and email send/skip. Review of saved replies found no additional material SOP or factual errors; some Chinese medical wording remained literal.

## Warm-service performance

| Measurement | Time |
| --- | --- |
| Conversation median | 2.580 s |
| Conversation p95 | 3.938 s |
| Conversation maximum | 4.408 s |
| Replay median | 0.156 s |
| Two sequential requests, total | 6.228 s |
| Two concurrent requests, total | 3.010 s |

Conversation timings include HTTP transit and both model calls. Cold starts and sustained load were not measured.

## Issues found and fixed

| Run | Finding | Change |
| --- | --- | --- |
| `728bbf1` | 11 failed assertions and one HTTP 502: registered-email references, incomplete email output, and claim/policy confusion. | Clarified concrete values versus references, invalid-address output, and claim identifiers. |
| `9d37871` | 11 failed assertions: refusal was treated as identity evidence; conditional consent became a final skip. | Clarified refusal evidence and conditional consent in the prompt and schema descriptions. |
| `3a8c104` | All 275 checks passed. | No language-specific business parser added. |

Earlier milestones: `619115b` passed 212/213 checks and exposed an omitted claim answer during wrap-up; `ad49206` fixed it and passed 225/225. These used the smaller 13-scenario suite.

The latest raw report is `test-results/render-3a8c104.json`; earlier reports remain locally in the ignored `test-results/` directory. See [Testing](testing.md) to reproduce.

## Limits

Results describe one run, not guaranteed future model behavior. Email delivery and human transfers are simulated. Live evaluation used OpenAI; Anthropic transport was tested with mocked HTTP responses, not a live account. Synthetic fixtures do not represent a production insurance system.
