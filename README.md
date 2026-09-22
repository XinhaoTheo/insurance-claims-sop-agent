# Insurance Claims SOP Agent

An insurance support demo with natural conversation and a code-controlled workflow. The LLM interprets messages and writes replies; the harness controls identity verification, case access, and email consent. Useful case hints are remembered across phases.

**Live website: [insurance-claims-sop-agent-d5gs.onrender.com](https://insurance-claims-sop-agent-d5gs.onrender.com)**

**GitHub repository: [XinhaoTheo/insurance-claims-sop-agent](https://github.com/XinhaoTheo/insurance-claims-sop-agent)**

No API key is required on this website; the owner funds model usage. The free service may need about a minute to wake. To use your own model or key, run the local version below.

Built with React, TypeScript, FastAPI, Pydantic, and SQLite. Local Docker and the hosted demo use the same application. Customer data is synthetic; email delivery and human transfers are simulated.

![Live demo showing a verified claim conversation and workflow state](docs/images/demo.png)

## Workflow

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 20, "rankSpacing": 30}, "themeVariables": {"fontSize": "14px"}}}%%
flowchart TB
    Message["Customer message"] --> Analyze["LLM: understand request"]
    Analyze --> SOP
    subgraph SOP["SOP harness"]
        direction TB
        Verify["VERIFY_ID"] --> Intent["RESOLVE_INTENT"] --> Process["PROCESS_CASE"] --> Post["POST_PROCESS"]
    end
    Data["Claim records<br/>and memory"] <--> SOP
    SOP --> Reply["LLM: phrase approved reply"]
    Reply --> UI["Chat UI"]

    classDef interface fill:#f1f5f9,stroke:#64748b,color:#0f172a;
    classDef model fill:#eef2ff,stroke:#6366f1,color:#312e81;
    classDef data fill:#fff7ed,stroke:#f59e0b,color:#7c2d12;
    classDef verify fill:#dbeafe,stroke:#3b82f6,color:#1e3a8a;
    classDef intent fill:#ede9fe,stroke:#8b5cf6,color:#4c1d95;
    classDef process fill:#ccfbf1,stroke:#14b8a6,color:#134e4a;
    classDef post fill:#dcfce7,stroke:#22c55e,color:#14532d;

    class Message,UI interface;
    class Analyze,Reply model;
    class Data data;
    class Verify verify;
    class Intent intent;
    class Process process;
    class Post post;
    style SOP fill:#f8fafc,stroke:#94a3b8,color:#0f172a;
    linkStyle default stroke:#64748b,stroke-width:1.5px;
```

Claim details require at least three matching identity categories. The agent can clarify, acknowledge frustration, and offer alternatives without skipping required steps.

## Local setup

Install Git and Docker Desktop, then start Docker. You need access to this repository, an OpenAI-compatible or Anthropic API key, and internet access for model requests.

### 1. Download and configure

```bash
git clone https://github.com/XinhaoTheo/insurance-claims-sop-agent.git
cd insurance-claims-sop-agent
cp .env.example .env
```

Edit the `.env` file in the repository root:

```dotenv
MODEL_API_PROTOCOL=openai
MODEL_API_KEY=your-api-key
MODEL_NAME=your-model-id
MODEL_BASE_URL=
```

Use `anthropic` for Claude. Leave the base URL blank for the selected protocol's official endpoint, or enter a compatible API root, usually ending in `/v1`. Use a model ID available to your account. Never commit your key.

### 2. Start the app

```bash
docker compose up --build -d
```

Open **[localhost:8000](http://localhost:8000)**. Docker builds the UI and backend and creates SQLite automatically; no separate database setup is needed.

Alternatively, start without model defaults and open **Model settings** in the UI. Enter the protocol, base URL, model, and key, then click **Test connection → Apply model**. These settings apply to the current conversation; temporary keys expire after one hour or a server restart.

### Change your model or API key

| Where | What to change | Apply the change |
| --- | --- | --- |
| Root `.env` file | `MODEL_API_KEY` for the key, `MODEL_NAME` for the model; change `MODEL_API_PROTOCOL` and `MODEL_BASE_URL` when switching providers. | Run `docker compose up -d`, reload, and select **New conversation**. |
| Local UI → **Model settings** | API protocol, base URL, model name, and API key. | Click **Test connection**, then **Apply model** for this conversation. |

The hosted website uses a fixed owner-managed model and does not show these settings. For your own hosted deployment, change the same variables in the hosting provider's environment settings.

### Try a conversation

```text
I'm the policyholder. My name is Margaret Chen, policy POL-9921.
I'm calling about my denied healthcare claim from January.
DOB is 1985-03-15, SSN last four is 4472.
```

Then ask **“What documents do I need?”**, followed by **“That's all, please summarize.”** Choose **Send mock email** or **Skip email**.

The app uses today's date. To demonstrate the sample before its appeal deadline, set `DEMO_DATE=2026-03-10` before starting a new conversation.

### Logs and shutdown

```bash
docker compose logs -f app
docker compose down
```

Local conversations survive normal restarts in a Docker volume. Render Free uses temporary storage, so conversations may be lost after sleep, restart, or redeployment.

## More details

- [Architecture](docs/architecture.md): modules, identity handling, and SOP gates.
- [Hosting](docs/hosting.md): deploy the same app on Render.
- [Testing](docs/testing.md): automated tests and real-model evaluation.
- [Evaluation results](docs/evaluation-results.md): latest cloud results and known limits.
- [Local API reference](http://localhost:8000/docs): endpoints for automated clients.
