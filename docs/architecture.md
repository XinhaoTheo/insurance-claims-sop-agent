# Architecture

Each normal active-session turn follows **model analysis → schema validation → SOP rules → model rendering → commit**. The LLM interprets language; server code controls verification, phases, case access, and actions. There is no offline parser or English/Chinese business branch.

See the [overall workflow](../README.md#workflow).

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 20, "rankSpacing": 30}, "themeVariables": {"fontSize": "14px"}}}%%
flowchart TD
    Input["Customer message"] --> Analyze["LLM: extract fields"]
    Analyze --> Validate["Check field formats"]
    Validate --> SOP["SOP: apply gates"]
    Memory["Saved hints"] <--> SOP
    SOP --> Facts["Read authorized facts"]
    Facts --> Plan["Build approved reply"]
    Plan --> Render["LLM: phrase reply"]
    Render --> Check{"Valid reply?"}
    Check -->|Yes| Save["Save turn and actions"]
    Check -->|No| Retry["Keep previous state"]
    Save --> UI["Reply to customer"]

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
%%{init: {"flowchart": {"nodeSpacing": 20, "rankSpacing": 30}, "themeVariables": {"fontSize": "14px"}}}%%
flowchart TD
    Hints["Saved intent and hints"] --> Query["Find customer's claims"]
    Query --> Count{"Matches?"}
    Count -->|None| Clarify["Ask for a clue"]
    Count -->|Several| Choose["Ask which claim"]
    Count -->|One| Bind["Select claim"]
    Bind --> Process["PROCESS_CASE"]

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```

### Process the case

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 20, "rankSpacing": 30}, "themeVariables": {"fontSize": "14px"}}}%%
flowchart TD
    Question["Follow-up question"] --> Topic["LLM: select topic"]
    Topic --> Guard["Check identity and ownership"]
    Guard --> Facts["Read facts and guidance"]
    Facts --> Plan["Build reply with sources"]
    Plan --> Render["LLM: phrase reply"]
    Render --> Check{"Valid reply?"}
    Check -->|Yes| Save["Save and continue"]
    Check -->|No| Retry["Keep state; allow retry"]

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```

## Identity: extraction, validation, matching

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 20, "rankSpacing": 30}, "themeVariables": {"fontSize": "14px"}}}%%
flowchart TD
    Message["Identity and claim question"] --> Extract["LLM: extract fields"]
    Extract --> Check["Check field formats"]
    Check --> Identity["Identity: temporary memory"]
    Check --> Hints["Case hints: saved state"]
    Identity --> Match["Match customer records"]
    Hints --> Later["Use after verification"]

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

If the caller explicitly says the displayed records are not theirs, the harness revokes verification, clears old identity values, case hints, selection, and draft, then asks for three fresh identity details or offers human support. Newly supplied details are retained. Missing search results and ordinary case changes do not trigger this recovery.

Evidence also supports transcript redaction. Extraction and evidence accuracy still depend on the model. If the model emits an invalid non-null value, schema validation fails the turn; the backend does not repair it.

### Verify identity

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 20, "rankSpacing": 30}, "themeVariables": {"fontSize": "14px"}}}%%
flowchart TD
    Input["New or corrected identity"] --> Match["Match customer records"]
    Match --> Gate{"3 matching categories?"}
    Gate -->|No| Ask["Clarify or offer alternatives"]
    Gate -->|Yes| Conflict{"Conflict or unresolved value?"}
    Conflict -->|Yes| Ask
    Conflict -->|No| Verify["Record verified customer"]
    Verify --> Next["RESOLVE_INTENT"]

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```

## Conversation and consent

Case hints are saved even during verification, but cannot authorize claim access. The model identifies scope, emotion, refusal, and intent. The harness declines unrelated questions, offers verification alternatives, and offers human support after repeated refusal or unrelated requests. Explicit human requests produce a simulated handoff.

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 20, "rankSpacing": 30}, "themeVariables": {"fontSize": "14px"}}}%%
flowchart TD
    Turn["Customer message"] --> Signals["LLM: identify signals"]
    Signals --> Memory["Save useful information"]
    Memory --> Human{"Human requested?"}
    Human -->|Yes| Transfer["Simulate handoff"]
    Human -->|No| Repeated{"Repeated refusal or diversion?"}
    Repeated -->|Yes| Offer["Offer human support"]
    Repeated -->|No| Reply["Acknowledge emotion;<br/>decline unrelated questions"]
    Reply --> Resume["Continue within SOP gates"]

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```

The renderer receives approved content and phrases it in the caller's language. It has no tools or phase authority. UI labels and the canonical email draft remain English. JSON validation checks structure, not factual or translation accuracy.

Sending requires a current, unconditional choice, valid verification, the registered recipient, and the current summary version. An address alone is not consent. Conditional requests remain undecided. A registered-address reference may authorize sending without supplying a new address. Invalid or different recipients block sending. New substantive discussion updates the draft before a further send choice.

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 20, "rankSpacing": 30}, "themeVariables": {"fontSize": "14px"}}}%%
flowchart TD
    Draft["Prepare summary draft"] --> Offer["Offer send or skip"]
    Offer --> Choice{"Customer choice?"}
    Choice -->|Undecided| Wait["Clarify; do not send"]
    Choice -->|Decided| SendNow{"Send now?"}
    SendNow -->|No| Skip["Skip and complete"]
    SendNow -->|Yes| Gate["Check sending gates"]
    Gate --> Send{"Mock send succeeds?"}
    Send -->|Yes| Render["Render confirmation"]
    Render --> Save["Save consent and outbox"]
    Send -->|No| Retry["Offer retry or skip"]

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```

UI buttons use `caller_action` values such as `send_summary`; they still pass through the SOP gates. Email delivery and handoff are simulated. The app does not change claim decisions, submit appeals, or transfer payments.

## Configuration, storage, and retries

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 20, "rankSpacing": 30}, "themeVariables": {"fontSize": "14px"}}}%%
flowchart TD
    Env["Environment defaults"] --> Resolve["config.py: merge and check"]
    UI["Explicit settings"] --> Resolve
    Resolve --> Config["ModelConfig"]
    Config --> Client["llm.py: call model API"]
    Constants["Shared constants"] --> Client
    Constants --> SOP["SOP and business tools"]

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```

- Configuration supports OpenAI-compatible Chat Completions and native Anthropic Messages. Explicit settings override defaults; changing endpoint or protocol cannot reuse a deployment key without an explicit key.
- Sponsored hosted mode fixes the model on the server and rejects visitor overrides. Visitor-key hosted mode restricts endpoints to an administrator allowlist.
- SQLite stores session snapshots, committed turn receipts, and the mock outbox. Recognized identity values are redacted from saved messages; raw verification values and visitor keys stay in memory.
- Visitor credentials expire one hour after configuration; identity memory expires after one hour without a successful turn. Active verification expires one hour after verification. Server-configured keys remain available after restarts.
- A session token controls access. A per-session lock serializes turns. Snapshot, receipt, and outbox writes commit together only after successful rendering.
- Retrying the same `turn_id`, message, and action returns the saved reply without repeating actions. Reusing the ID with different input returns HTTP 409. Model failures leave the prior committed state intact.

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 20, "rankSpacing": 30}, "themeVariables": {"fontSize": "14px"}}}%%
flowchart TD
    Request["Message and turn ID"] --> Auth["Check session access"]
    Auth --> Lock["Lock this session"]
    Lock --> Saved{"Turn already saved?"}
    Saved -->|Yes| Same{"Same input?"}
    Same -->|Yes| Replay["Return saved reply"]
    Same -->|No| Conflict["HTTP 409"]
    Saved -->|No| Run["Analyze, apply SOP,<br/>and render reply"]
    Run --> Valid{"Successful?"}
    Valid -->|No| Keep["Keep previous state"]
    Valid -->|Yes| Save["Save state, reply,<br/>and mock outbox together"]

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```

Run one worker and one instance: locks and temporary credentials are process-local. Local Docker persists SQLite in a volume; Render Free uses temporary storage. See [Hosting](hosting.md) and [Testing](testing.md).

## Local and cloud deployment

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 20, "rankSpacing": 30}, "themeVariables": {"fontSize": "14px"}}}%%
flowchart TD
    Source["GitHub source"] --> Build["Build React and Python app"]
    Build --> Image["Shared Docker image"]
    Image --> Local["Local: one worker"]
    Image --> Cloud["Cloud: one instance"]
    Local --> DetailsLocal["localhost:8000<br/>SQLite in Docker volume"]
    Cloud --> DetailsCloud["Public HTTPS URL<br/>Temporary SQLite on Render Free"]

    classDef default fill:#eef2ff,stroke:#818cf8,color:#1e1b4b;
```
