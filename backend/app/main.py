import asyncio
import copy
import secrets
import time
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .business import FixtureRepository
from .config import settings
from .harness import Harness, event, identity_reset, new_snapshot, public_snapshot, redact
from .llm import ModelError, analyze_turn, test_connection, validate_config
from .schemas import MessageRequest, ModelConfig, SessionCreate
from .storage import Store


def create_app(overrides: dict | None = None):
    cfg = {**settings(), **(overrides or {})}
    app = FastAPI(title="Insurance Claims SOP Harness", version="0.1.0",
                  description="Local demo. Offline mode is deterministic; live mode uses your OpenAI-compatible API token. Email and human handoff are simulated.")
    store = Store(str(cfg["database"]))
    harness = Harness(FixtureRepository(cfg["fixtures"]), email_failure=cfg["email_failure"])
    credentials: dict[str, tuple[dict, float]] = {}
    identities: dict[str, tuple[dict, float]] = {}
    locks: dict[str, asyncio.Lock] = {}
    app.state.store = store
    app.state.harness = harness
    app.state.identities = identities
    app.state.credentials = credentials
    app.state.settings = cfg

    def cleanup():
        now = time.monotonic()
        for memory in (credentials, identities):
            for key, (_, expires) in list(memory.items()):
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
        if state["verified"] and time.time() - (state.get("verified_at") or 0) > cfg["secret_ttl"]:
            identity_reset(snapshot, "Verification expired; protected access closed.")
            state["identity_collected"] = []
            identities.pop(session_id, None)
            store.save(session_id, snapshot)
        if not state["verified"] and session_id not in identities and state["identity_collected"]:
            state["identity_collected"] = []
            event(snapshot, "identity_expired", "Unverified identity inputs were cleared from memory; case hints remain.")
            store.save(session_id, snapshot)
        return snapshot

    def model_config(payload):
        values = payload.model_dump(exclude_none=True)
        values.pop("demo_date", None)
        values["base_url"] = values.get("base_url") or cfg["base_url"]
        values["model"] = values.get("model") or cfg["model"]
        # Never forward the deployment key to a caller-selected endpoint.
        supplied_key = values.get("api_key")
        if not supplied_key and values["base_url"].rstrip("/") != cfg["base_url"].rstrip("/"):
            if values["mode"] == "live":
                raise HTTPException(400, "Supply your own API key for a custom endpoint.")
        values["api_key"] = supplied_key or cfg["api_key"]
        try:
            return validate_config(values)
        except ModelError as exc:
            raise HTTPException(400, str(exc)) from None

    def resolve_model(session_id, state):
        if state["model_mode"] == "offline":
            return {"mode": "offline"}
        if session_id in credentials:
            return credentials[session_id][0]
        raise HTTPException(409, "Model credentials expired or the server restarted. Reconnect your model in Settings; your conversation is preserved.")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse(status_code=422, content={"detail": "Invalid request: " + "; ".join(
            ".".join(str(x) for x in error["loc"]) + ": " + error["msg"] for error in exc.errors())})

    @app.middleware("http")
    async def local_safety(request: Request, call_next):
        # The initial delivery is bound to localhost. No wildcard CORS or public deployment claims.
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    async def health():
        return {"status": "ok", "email_mode": "mock", "version": "0.1.0"}

    @app.get("/api/config")
    async def configuration():
        return {"default_mode": "live" if cfg["api_key"] and cfg["model"] else "offline",
                "base_url": cfg["base_url"], "model": cfg["model"],
                "configured": bool(cfg["api_key"] and cfg["model"]), "demo_date": cfg["demo_date"], "email_mode": "mock"}

    @app.post("/api/models/test")
    async def check_model(payload: ModelConfig):
        config = model_config(payload)
        try:
            return await test_connection(config)
        except ModelError as exc:
            raise HTTPException(502, str(exc)) from None

    @app.post("/api/sessions", status_code=201)
    async def create_session(payload: SessionCreate):
        cleanup()
        config = model_config(payload)
        try:
            demo_date = date.fromisoformat(payload.demo_date or cfg["demo_date"]).isoformat()
        except ValueError:
            raise HTTPException(422, "demo_date must be a valid YYYY-MM-DD date.") from None
        session_id, access = secrets.token_urlsafe(18), secrets.token_urlsafe(32)
        snapshot = new_snapshot(session_id, config["mode"], demo_date)
        event(snapshot, "session_started", f"Mode: {config['mode']}; business date: {demo_date}; email/handoff: simulated.")
        if config["mode"] == "live":
            credentials[session_id] = (config, time.monotonic() + cfg["secret_ttl"])
        identities[session_id] = ({}, time.monotonic() + cfg["secret_ttl"])
        store.create(session_id, access, snapshot)
        return {**public_snapshot(snapshot), "access_token": access}

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str, access: str = Depends(token)):
        return public_snapshot(load(session_id, access))

    @app.get("/api/sessions/{session_id}/trace")
    async def get_trace(session_id: str, access: str = Depends(token)):
        snapshot = load(session_id, access)
        return {"events": snapshot["events"], "state": public_snapshot(snapshot)["state"]}

    @app.post("/api/sessions/{session_id}/model")
    async def configure_model(session_id: str, payload: ModelConfig, access: str = Depends(token)):
        async with locks.setdefault(session_id, asyncio.Lock()):
            snapshot = load(session_id, access)
            config = model_config(payload)
            credentials.pop(session_id, None)
            if config["mode"] == "live":
                credentials[session_id] = (config, time.monotonic() + cfg["secret_ttl"])
            snapshot["state"]["model_mode"] = config["mode"]
            event(snapshot, "model_configured", f"Switched to {config['mode']} mode; no API key stored in database.")
            store.save(session_id, snapshot)
            return public_snapshot(snapshot)

    @app.delete("/api/sessions/{session_id}/model")
    async def clear_model(session_id: str, access: str = Depends(token)):
        async with locks.setdefault(session_id, asyncio.Lock()):
            snapshot = load(session_id, access)
            credentials.pop(session_id, None)
            snapshot["state"]["model_mode"] = "offline"
            event(snapshot, "credentials_cleared", "Temporary model credentials removed; explicitly switched to offline fixture mode.")
            store.save(session_id, snapshot)
            return public_snapshot(snapshot)

    @app.post("/api/sessions/{session_id}/messages")
    async def message(session_id: str, payload: MessageRequest, access: str = Depends(token)):
        if not payload.message.strip():
            raise HTTPException(422, "A nonempty message is required.")
        async with locks.setdefault(session_id, asyncio.Lock()):
            snapshot = load(session_id, access)
            try:
                receipt = store.receipt(session_id, payload.turn_id, payload.message)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            if receipt:
                return receipt
            if len(snapshot["messages"]) > 200:
                raise HTTPException(409, "Session message limit reached. Start a new session.")
            config = resolve_model(session_id, snapshot["state"])
            state = snapshot["state"]
            context = {key: state[key] for key in ("phase", "pending", "case_hints", "intent", "language", "identity_collected")}
            context["previous_assistant"] = next((m["content"] for m in reversed(snapshot["messages"]) if m["role"] == "assistant"), "")
            try:
                analysis = await analyze_turn(payload.message, context, config)
            except ModelError as exc:
                raise HTTPException(502, str(exc)) from None
            identity = copy.deepcopy(identities.get(session_id, ({}, 0))[0])
            try:
                reply = harness.run(snapshot, analysis, identity, payload.message, payload.turn_id)
            except (PermissionError, ValueError):
                raise HTTPException(409, "The requested action could not pass the workflow checks. No action was committed; please clarify your request.") from None
            except (RuntimeError, OSError, TimeoutError):
                raise HTTPException(502, "A business tool is unavailable. No action was committed. Please retry or request human help.") from None
            identities[session_id] = (identity, time.monotonic() + cfg["secret_ttl"])
            snapshot["messages"].extend([
                {"role": "user", "content": redact(payload.message, {**identity, **analysis.identity.model_dump(exclude_none=True)}), "turn_id": payload.turn_id},
                {"role": "assistant", "content": reply, "turn_id": payload.turn_id},
            ])
            response = {**public_snapshot(snapshot), "reply": reply}
            store.save(session_id, snapshot, payload.turn_id, payload.message, response)
            if state["status"] in ("completed", "handoff_requested"):
                identities.pop(session_id, None)
                credentials.pop(session_id, None)
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
