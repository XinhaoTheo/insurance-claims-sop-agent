# Architecture

Each normal active-session turn follows **model analysis → schema validation → SOP rules → model rendering → commit**. The LLM interprets language; server code controls verification, phases, case access, and actions. There is no offline parser or English/Chinese business branch.

See the [overall workflow](../README.md#workflow).

```mermaid
flowchart TD
    User["Customer or API client"] --> API["API: check access and retries"]
    API --> Parse["LLM: extract information"]
    Parse --> Schema["Check output format"]
    Schema --> Harness["SOP harness"]
    Memory["State and caller hints"] <--> Harness
    Harness --> Gates["Verification and action gates"]
    Gates --> Tools["Customer / claim / guidance tools"]
    Tools --> Fixtures["Read-only synthetic JSON fixtures"]
    Tools --> Facts["Authorized facts and sources"]
    Facts --> Plan["Approved reply content"]
    Harness --> Plan
    Plan --> Render["LLM: phrase the reply"]
    Render --> Check["Check reply format"]
    Check --> Persist["Save turn and simulated actions together"]
    Render -->|Failure| Rollback["Failure: keep previous saved state"]
    Persist --> SQLite[("SQLite: durable locally, temporary on Render Free")]
    Persist --> User

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```


## Modules

| File | Responsibility |
| --- | --- |
| `frontend/src/` | Chat, model settings, workflow state, and activity log. |
| `backend/app/main.py` | HTTP API, session access, per-session locking, and turn orchestration. |
| `config.py` | Merge environment defaults with explicit settings; validate model configuration. |
| `llm.py` | Analyze messages and render approved replies through OpenAI-compatible or Anthropic APIs. |
| `schemas.py` | Validate model observations, identity formats, and API payloads. |
| `harness.py` | Apply SOP rules, retain hints, prepare replies and summaries. |
| `business.py` | Match identity and retrieve authorized fixture facts. |
| `storage.py` | SQLite snapshots, retry receipts, and simulated email outbox. |
| `constants.py` | Shared fields, mappings, and protocol constants; no credentials. |

`Harness.run()` receives structured analysis, proposed session state, temporary identity memory, and a turn ID. It updates the proposed state and returns approved reply content. It does not call the model or commit the database transaction.

## Workflow

| Phase | Mechanism |
| --- | --- |
| `VERIFY_ID` | Require three distinct matching categories: name, DOB, phone, email, or SSN last four. All supplied fields must agree with one customer. Policy number does not count. |
| `RESOLVE_INTENT` | Reuse saved hints; search only that customer's claims. Ask for clarification if no unique case matches. |
| `PROCESS_CASE` | Select a bounded topic and answer from claim records and guidance. Record source identifiers. |
| `POST_PROCESS` | Prepare a versioned summary of the discussion, claim status, outcome, and next steps. Offer send or skip. |

A turn may complete several phases in order. Corrections or expired verification revoke access. Protected tools also check the verified customer and case ownership. Fixture dates are creation dates, not service dates.

### Resolve intent

```mermaid
flowchart TD
    Saved["Remembered intent and caller case hints"] --> Query["Query only verified customer's cases"]
    Query --> Count{"Matching cases?"}
    Count -->|None| Clarify["Ask for another case clue"]
    Count -->|Multiple| Distinguish["Ask which claim or creation year"]
    Count -->|One| Bind["Bind selected case"]
    Bind --> Process["PROCESS_CASE"]

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```

### Process the case

```mermaid
flowchart LR
    Question["Natural-language follow-up"] --> Topic["LLM: choose a supported topic"]
    Topic --> Guard["Recheck verification and claim ownership"]
    Guard --> Data["Claim fields + relevant guidance"]
    Data --> Approved["Harness builds approved_reply"]
    Approved --> Sources["Record fixture source identifiers"]
    Approved --> Render["LLM: phrase approved facts"]
    Render --> Validate["Check reply format"]
    Validate --> Follow["Commit turn; continue questions or finish"]
    Render -->|Failure| Retry["Rollback; caller may retry"]

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```

## Identity: extraction, validation, matching

```mermaid
flowchart LR
    Utterance["Caller: identity + denied January claim"] --> Extract["LLM: extract identity and case hints"]
    Extract --> Validate["Check field formats"]
    Validate --> PII["Identity values: temporary memory"]
    Validate --> Hints["Case hints: saved with the conversation"]
    Hints --> Later["Reuse after verification"]
    PII --> Verify["Match customer records"]

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```

The model standardizes identity values; Pydantic checks their format; business code matches them against customer records. There is no business-layer `normalize()` that repairs input.

| Caller input | Model output | Harness behavior |
| --- | --- | --- |
| A concrete, valid identity value | Canonical `identity` value and verbatim `identity_evidence` | Match against records. |
| An invalid or ambiguous value, such as `broken@` | Null identity value; original text in evidence | Retain an unresolved marker and ask for clarification. |
| “My verified email” or “I refuse to give my SSN” | Both corresponding fields null | Do not treat the reference or refusal as a new value. |
| A claim number | `hints.case_id` | Do not treat it as a policy number. |

Canonical formats include ISO dates, US phones as `+1` plus ten digits, lowercase email, uppercase policy numbers, and four-digit SSN suffixes. Names use casefolding and single spaces for matching. Customer matching sets are prepared on first use.

An unresolved correction clears the old value and blocks verification until clarified, even when three other fields match. In `POST_PROCESS`, a proposed recipient is handled separately and does not replace the verified identity email.

Evidence also supports transcript redaction. Extraction and evidence accuracy still depend on the model. If the model emits an invalid non-null value, schema validation fails the turn; the backend does not repair it.

### Verify identity

```mermaid
flowchart TD
    Input["New or corrected identity fields"] --> Match["Match standardized values to a unique customer"]
    Match --> Gate{"3 matching identity categories, no conflict or unresolved value?"}
    Gate -->|No| Stay["Ask for clarification or offer alternatives"]
    Stay --> Input
    Gate -->|Yes| Verified["Server records verified customer and time"]
    Verified --> Next["RESOLVE_INTENT"]

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```

## Conversation and consent

Case hints are saved even during verification, but cannot authorize claim access. The model identifies scope, emotion, refusal, and intent. The harness declines unrelated questions, offers verification alternatives, and offers human support after repeated refusal or unrelated requests. Explicit human requests produce a simulated handoff.

```mermaid
flowchart TD
    Turn["Each caller turn"] --> Signals["Scope, emotion, refusal, human request"]
    Signals --> Store["Retain useful caller information"]
    Store --> Decision{"Appropriate response"}
    Decision -->|Frustrated / anxious / confused| Empathy["Acknowledge, explain, offer permitted alternatives"]
    Decision -->|Unrelated| Decline["Decline and resume pending question"]
    Decision -->|Repeated refusal / unrelated| Offer["Offer human or return to claim"]
    Decision -->|Explicit human request| Human["Record simulated handoff; close session"]
    Empathy --> Gate["All identity and consent gates still apply"]
    Decline --> Gate

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```

The renderer receives approved content and phrases it in the caller's language. It has no tools or phase authority. UI labels and the canonical email draft remain English. JSON validation checks structure, not factual or translation accuracy.

Sending requires a current, unconditional choice, valid verification, the registered recipient, and the current summary version. An address alone is not consent. Conditional requests remain undecided. A registered-address reference may authorize sending without supplying a new address. Invalid or different recipients block sending. New substantive discussion updates the draft before a further send choice.

```mermaid
flowchart TD
    Done["Caller finishes case discussion"] --> Draft["Build versioned factual summary"]
    Draft --> Offer["Offer send to recorded address or skip"]
    Offer --> Analysis["Read model consent choice or button action"]
    Analysis --> Choice{"Send now, skip, or undecided?"}
    Choice -->|Unclear| Offer
    Choice -->|Skip| Skip["Record skipped; complete"]
    Choice -->|Send| Recipient["Check phase, identity, recipient, and draft version"]
    Recipient --> Delivery{"Simulated send result"}
    Delivery -->|Success| Render["Render confirmation; validate output"]
    Render --> Outbox["Commit consent and outbox once; complete"]
    Delivery -->|Failure| Retry["Report failure; offer retry or skip"]
    Retry --> Offer

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```

UI buttons use `caller_action` values such as `send_summary`; they still pass through the SOP gates. Email delivery and handoff are simulated. The app does not change claim decisions, submit appeals, or transfer payments.

## Configuration, storage, and retries

```mermaid
flowchart LR
    Env["Environment defaults"] --> Resolve["config.py: merge and validate"]
    UI["Explicit UI or API settings"] --> Resolve
    Resolve --> Config["Resolved ModelConfig"]
    Config --> LLM["llm.py: call supplied protocol and model"]
    Constants["constants.py: workflow fields, context keys, protocol limits"] --> LLM
    Constants --> Harness["SOP and business tools"]

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```

- Configuration supports OpenAI-compatible Chat Completions and native Anthropic Messages. Explicit settings override defaults; changing endpoint or protocol cannot reuse a deployment key without an explicit key.
- Sponsored hosted mode fixes the model on the server and rejects visitor overrides. Visitor-key hosted mode restricts endpoints to an administrator allowlist.
- SQLite stores session snapshots, committed turn receipts, and the mock outbox. Recognized identity values are redacted from saved messages; raw verification values and visitor keys stay in memory.
- Visitor credentials expire one hour after configuration; identity memory expires after one hour without a successful turn. Active verification expires one hour after verification. Server-configured keys remain available after restarts.
- A session token controls access. A per-session lock serializes turns. Snapshot, receipt, and outbox writes commit together only after successful rendering.
- Retrying the same `turn_id`, message, and action returns the saved reply without repeating actions. Reusing the ID with different input returns HTTP 409. Model failures leave the prior committed state intact.

```mermaid
flowchart LR
    Request["Session token + message + turn_id + optional caller_action"] --> Auth["Compare hashed session token"]
    Auth --> Lock["Acquire per-session lock"]
    Lock --> Existing{"Turn already saved?"}
    Existing -->|Same message and action| Replay["Original reply + current snapshot"]
    Existing -->|Different message or action| Conflict["HTTP 409"]
    Existing -->|No| Execute["Analyze; run SOP on proposed state"]
    Execute --> Render["Render approved reply; validate result"]
    Render -->|Success| Transaction["Save state, reply, and mock outbox together"]
    Render -->|Failure| Rollback["Discard proposed state and actions"]
    Transaction --> Response["Return redacted snapshot"]

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```

Run one worker and one instance: locks and temporary credentials are process-local. Local Docker persists SQLite in a volume; Render Free uses temporary storage. See [Hosting](hosting.md) and [Testing](testing.md).

## Local and cloud deployment

```mermaid
flowchart TD
    Source["GitHub source"] --> Node["Node 22: npm ci + frontend build"]
    Source --> Python["Python 3.12: pinned backend dependencies"]
    Node --> Image["Shared application image"]
    Python --> Image
    Image --> Container["One Uvicorn worker"]
    Env["Runtime .env and explicit session settings"] --> Config["config.py"]
    Config --> Container
    Container --> UI["Browser localhost:8000"]
    Image --> Cloud["Cloud service: one instance"]
    Cloud --> URL["Browser: public HTTPS URL"]
    Container --> Volume[("claims-data /data/insurance.db")]
    Container --> Model["Chosen model API"]
    Config --> Cloud
    Cloud --> Temporary[("Temporary SQLite on Render Free")]
    Cloud --> Model

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```
