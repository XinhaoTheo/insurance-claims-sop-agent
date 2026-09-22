import asyncio
import secrets
import time
from collections import deque
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .business import FixtureRepository
from .config import ConfigurationError, PROTOCOL_BASE_URLS, resolve_model_config, settings
from .constants import TERMINAL_STATUSES
from .harness import Harness, event, identity_reset, new_snapshot, public_snapshot, redact
from .llm import ModelError, analyze_turn, render_reply, test_connection
from .schemas import MessageRequest, ModelConfig, SessionCreate
from .storage import Store


def create_app(overrides: dict | None = None):
    cfg = {**settings(), **(overrides or {})}
    app = FastAPI(title="Insurance Claims SOP Harness", version="0.1.0",
                  description="SOP demo using a configured OpenAI-compatible or Anthropic model. Email and human handoff are simulated.")
    store = Store(str(cfg["database"]))
    harness = Harness(FixtureRepository(cfg["fixtures"]), email_failure=cfg["email_failure"])
    credentials: dict[str, tuple[ModelConfig, float]] = {}
    identities: dict[str, tuple[dict, float]] = {}
    locks: dict[str, asyncio.Lock] = {}
    app.state.store = store
    app.state.harness = harness
    app.state.identities = identities
    app.state.credentials = credentials
    app.state.settings = cfg

    if cfg["hosted"]:
        recent_requests: deque[float] = deque()

        @app.middleware("http")
        async def hosted_requests(request, call_next):
            if request.url.path.startswith("/api/") and request.method == "POST":
                now = time.monotonic()
                while recent_requests and recent_requests[0] <= now - 60:
                    recent_requests.popleft()
                if len(recent_requests) >= 60:
                    return JSONResponse(status_code=429,
                                        content={"detail": "The shared demo is busy. Please retry in one minute."},
                                        headers={"Retry-After": "60", "Cache-Control": "no-store"})
                recent_requests.append(now)
            response = await call_next(request)
            if request.url.path.startswith("/api/"):
                response.headers["Cache-Control"] = "no-store"
            return response

    def model_configuration(supplied=None):
        defaults = cfg["model_config"]
        if cfg["hosted"]:
            defaults = defaults.model_copy(update={"api_key": None})
        return resolve_model_config(defaults, supplied, allowed_base_urls=cfg["allowed_model_base_urls"])

    def cleanup():
        now = time.monotonic()
        for memory in (credentials, identities):
            for key, (value, expires) in list(memory.items()):
                if expires < now:
                    memory.pop(key, None)

    def token(authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "A session access token is required.")
        return authorization[7:]

    def load(session_id, access):
        cleanup()
        snapshot = store.load(session_id, access)
        if snapshot is None:
            raise HTTPException(404, "Session not found or access denied.")
        state = snapshot["state"]
        if state["status"] not in TERMINAL_STATUSES and state["verified"] and time.time() - state["verified_at"] > cfg["secret_ttl"]:
            identity_reset(snapshot, "Verification expired; protected access closed.")
            state["identity_collected"] = []
            identities.pop(session_id, None)
            store.save(session_id, snapshot)
        if state["status"] not in TERMINAL_STATUSES and not state["verified"] and session_id not in identities and state["identity_collected"]:
            state["identity_collected"] = []
            event(snapshot, "identity_expired", "Unverified identity inputs were cleared from memory; case hints remain.")
            store.save(session_id, snapshot)
        return snapshot

    def resolve_model(session_id):
        if session_id in credentials:
            return credentials[session_id][0]
        raise HTTPException(409, "Configure a model in Settings before chatting. Your conversation is preserved.")

    def session_response(snapshot):
        return {**public_snapshot(snapshot), "model_configured": snapshot["session_id"] in credentials}

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse(status_code=422, content={"detail": "Invalid request: " + "; ".join(
            ".".join(str(x) for x in error["loc"]) + ": " + error["msg"] for error in exc.errors())})

    @app.exception_handler(ConfigurationError)
    async def invalid_configuration(request, exc):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(ModelError)
    async def model_failed(request, exc):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(PermissionError)
    async def workflow_denied(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health")
    async def health():
        return {"status": "ok", "email_mode": "mock", "version": "0.1.0"}

    @app.get("/api/config")
    async def configuration():
        defaults = cfg["model_config"]
        try:
            model_configuration()
            configured = True
        except ConfigurationError:
            configured = False
        base_url = defaults.base_url
        if cfg["hosted"] and base_url not in cfg["allowed_model_base_urls"]:
            official_url = PROTOCOL_BASE_URLS[defaults.api_protocol or "openai"]
            base_url = official_url if official_url in cfg["allowed_model_base_urls"] else sorted(cfg["allowed_model_base_urls"])[0]
        return {"api_protocol": defaults.api_protocol, "base_url": base_url, "model": defaults.model,
                "configured": configured, "protocol_base_urls": PROTOCOL_BASE_URLS,
                "hosted": cfg["hosted"], "allowed_model_base_urls": sorted(cfg["allowed_model_base_urls"]) if cfg["hosted"] else None,
                "demo_date": cfg["demo_date"], "email_mode": "mock"}

    @app.post("/api/models/test")
    async def check_model(payload: ModelConfig):
        config = model_configuration(payload)
        return await test_connection(config)

    @app.post("/api/sessions", status_code=201)
    async def create_session(payload: SessionCreate):
        cleanup()
        supplied = ModelConfig(**payload.model_dump(exclude_unset=True, exclude={"demo_date"}))
        try:
            config = model_configuration(supplied)
        except ConfigurationError:
            if supplied.model_fields_set:
                raise
            config = None
        try:
            demo_date = date.fromisoformat(payload.demo_date or cfg["demo_date"]).isoformat()
        except ValueError:
            raise HTTPException(422, "demo_date must be a valid YYYY-MM-DD date.") from None
        session_id, access = secrets.token_urlsafe(18), secrets.token_urlsafe(32)
        snapshot = new_snapshot(session_id, demo_date)
        event(snapshot, "session_started", f"Business date: {demo_date}; email/handoff: simulated.")
        if config is not None:
            credentials[session_id] = (config, time.monotonic() + cfg["secret_ttl"])
        identities[session_id] = ({}, time.monotonic() + cfg["secret_ttl"])
        store.create(session_id, access, snapshot)
        return {**session_response(snapshot), "access_token": access}

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str, access: str = Depends(token)):
        return session_response(load(session_id, access))

    @app.get("/api/sessions/{session_id}/trace")
    async def get_trace(session_id: str, access: str = Depends(token)):
        snapshot = load(session_id, access)
        return {"events": snapshot["events"], "state": public_snapshot(snapshot)["state"]}

    @app.post("/api/sessions/{session_id}/model")
    async def configure_model(session_id: str, payload: ModelConfig, access: str = Depends(token)):
        async with locks.setdefault(session_id, asyncio.Lock()):
            snapshot = load(session_id, access)
            config = model_configuration(payload)
            credentials[session_id] = (config, time.monotonic() + cfg["secret_ttl"])
            event(snapshot, "model_configured", f"Configured {config.api_protocol}; no API key stored in database.")
            store.save(session_id, snapshot)
            return session_response(snapshot)

    @app.delete("/api/sessions/{session_id}/model")
    async def clear_model(session_id: str, access: str = Depends(token)):
        async with locks.setdefault(session_id, asyncio.Lock()):
            snapshot = load(session_id, access)
            credentials.pop(session_id, None)
            event(snapshot, "credentials_cleared", "Model disconnected. Configure a model to continue chatting.")
            store.save(session_id, snapshot)
            return session_response(snapshot)

    @app.post("/api/sessions/{session_id}/messages")
    async def message(session_id: str, payload: MessageRequest, access: str = Depends(token)):
        if not payload.message.strip():
            raise HTTPException(422, "A nonempty message is required.")
        async with locks.setdefault(session_id, asyncio.Lock()):
            snapshot = load(session_id, access)
            receipt_input = payload.model_dump_json(exclude={"turn_id"})
            try:
                receipt = store.receipt(session_id, payload.turn_id, receipt_input)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            if receipt:
                return {**session_response(snapshot), "reply": receipt["reply"]}
            state = snapshot["state"]
            if state["status"] in TERMINAL_STATUSES:
                return {**session_response(snapshot), "reply": snapshot["messages"][-1]["content"]}
            context = {key: state[key] for key in ("phase", "pending", "case_hints", "intent", "identity_collected")}
            context["previous_assistant"] = snapshot["messages"][-1]["content"]
            context["previous_caller"] = next((m["content"] for m in reversed(snapshot["messages"]) if m["role"] == "user" and not m.get("caller_action")), "")
            context["caller_action"] = payload.caller_action
            config = resolve_model(session_id)
            analysis = await analyze_turn(payload.message, context, config)
            if payload.caller_action == "finish_case":
                analysis.finish = True
            elif payload.caller_action:
                analysis.email_choice = "send" if payload.caller_action == "send_summary" else "skip"
            identity = identities.get(session_id, ({}, 0))[0].copy()
            approved_reply = harness.run(snapshot, analysis, identity, payload.turn_id)
            pii = {**identity, **analysis.identity.model_dump(exclude_none=True)}
            evidence = analysis.identity_evidence.model_dump(exclude_none=True)
            caller_text = redact(payload.message, pii, evidence)
            reply = await render_reply(approved_reply, caller_text, context, config)
            identities[session_id] = (identity, time.monotonic() + cfg["secret_ttl"])
            snapshot["messages"].extend([
                {"role": "user", "content": caller_text, "turn_id": payload.turn_id, "caller_action": payload.caller_action},
                {"role": "assistant", "content": reply, "turn_id": payload.turn_id},
            ])
            if state["status"] in TERMINAL_STATUSES:
                identities.pop(session_id, None)
                credentials.pop(session_id, None)
            response = {**session_response(snapshot), "reply": reply}
            store.save(session_id, snapshot, payload.turn_id, receipt_input, response)
            return response

    static = Path(cfg["static"])
    if (static / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    async def index():
        if (static / "index.html").exists():
            return FileResponse(static / "index.html")
        return JSONResponse({"message": "API is ready. Build the frontend or run its Vite dev server.", "docs": "/docs"})

    return app


app = create_app()
