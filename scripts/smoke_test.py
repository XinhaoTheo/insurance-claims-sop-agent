#!/usr/bin/env python3
"""Exercise the HTTP workflow using the configured real model.

Run against an already running app:
    backend/.venv/bin/python scripts/smoke_test.py --base-url http://127.0.0.1:8000
Creates small synthetic test sessions in that instance's SQLite database.
Loads MODEL_API_PROTOCOL, MODEL_API_KEY, MODEL_BASE_URL, and MODEL_NAME from the
environment or project .env through the same configuration layer as the server.
Requests go to the configured model provider and may incur API charges.
"""

import argparse
import json
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import ConfigurationError, resolve_model_config, settings


SAMPLE = (
    "I’m the policyholder. My name is Margaret Chen, policy POL-9921. "
    "I’m calling about my denied healthcare claim from January. "
    "DOB is 1985-03-15, SSN last four is 4472."
)


class Client:
    def __init__(self, base_url, model_config):
        self.base_url = base_url.rstrip("/")
        self.model_config = model_config

    def request(self, method, path, body=None, token=None, expected=200):
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(
            self.base_url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=150) as response:
                status, data = response.status, json.load(response)
        except HTTPError as exc:
            status = exc.code
            try:
                data = json.load(exc)
            except (ValueError, TypeError):
                data = {}
        except (URLError, TimeoutError):
            raise RuntimeError("Cannot reach the app or the model request timed out. Check the server and model connection.") from None
        if status != expected:
            # Do not dump full session bodies or tokens on error.
            raise AssertionError(f"{method} {path}: expected HTTP {expected}, got {status}")
        return data

    def session(self, demo_date="2026-03-10"):
        return self.request("POST", "/api/sessions", {
            **self.model_config.model_dump(), "demo_date": demo_date,
        }, expected=201)

    def say(self, session, text, turn_id=None, expected=200, caller_action=None):
        return self.request(
            "POST", f"/api/sessions/{session['session_id']}/messages",
            {"message": text, "turn_id": turn_id or str(uuid.uuid4()), "caller_action": caller_action},
            token=session["access_token"], expected=expected,
        )


def check(condition, label):
    if not condition:
        raise AssertionError(label)
    print("PASS  " + label)


def multilingual_scenario(client):
    """Check multilingual state transitions; print replies for human language review."""
    session = client.session()
    result = client.say(session, "Soy Margaret Chen. Mi reclamación médica de enero fue denegada.")
    check(not result["state"]["verified"] and result["state"]["case_hints"].get("month") == 1,
          "Spanish partial identity retains the case hint behind verification")
    result = client.say(session, "Nací el 15 de marzo de 1985. Los últimos cuatro dígitos de mi SSN son 4472.")
    check(result["state"]["verified"] and result["state"]["selected_case_id"] == "CL-2048",
          "sourced natural birthdate completes identity and resolves the remembered case")
    check("15 de marzo de 1985" not in str(result["messages"]), "natural birthdate evidence is redacted")
    print("REVIEW multilingual claim reply: " + result["reply"])
    result = client.say(session, "No tengo más preguntas. Hemos terminado.")
    check(result["state"]["phase"] == "POST_PROCESS", "Spanish completion offers an optional summary")
    result = client.say(session, "Envíalo solo si aprueban mi reclamación.")
    check(result["state"]["email_status"] == "awaiting_choice", "conditional Spanish consent does not send")
    result = client.say(session, "Send summary", caller_action="send_summary", turn_id="spanish-summary")
    check(result["state"]["email_status"] == "simulated_sent", "explicit UI choice uses the same consent gate")
    print("REVIEW button reply should retain the preceding conversation language: " + result["reply"])


def run(base_url, multilingual=False):
    config = resolve_model_config(settings()["model_config"])
    client = Client(base_url, config)
    client.request("POST", "/api/models/test", config.model_dump())
    print("PASS  real model connection and JSON output")
    health = client.request("GET", "/health")
    check(health["status"] == "ok" and health["email_mode"] == "mock", "health and explicit mock email mode")

    session = client.session()
    check(session["model_configured"], "session uses the configured real model")
    check(session["state"]["phase"] == "VERIFY_ID", "new session starts at VERIFY_ID")
    client.request("GET", f"/api/sessions/{session['session_id']}", expected=401)
    print("PASS  session endpoint requires its access token")

    result = client.say(session, SAMPLE, turn_id="sample-verification")
    check(result["state"]["verified"] and result["state"]["phase"] == "PROCESS_CASE", "three matching fields verify before case processing")
    check(result["state"]["selected_case_id"] == "CL-2048", "remembered January/denied/healthcare hint selects the case")
    check("pathology" in result["reply"].lower(), "claim explanation uses recorded facts")
    replay = client.say(session, SAMPLE, turn_id="sample-verification")
    check(replay == result, "same turn_id and message replay the committed response")
    client.say(session, "a different message", turn_id="sample-verification", expected=409)
    print("PASS  conflicting turn_id is rejected")

    result = client.say(session, "What documents do I need?")
    check("pathology" in result["reply"].lower(), "natural document question selects grounded guidance")

    result = client.say(session, "That's all, no more questions.")
    check(result["state"]["phase"] == "POST_PROCESS" and result["state"]["email_status"] == "awaiting_choice", "post-process waits for email consent")
    check(bool(result["email_summary"]["body"]), "summary includes an inspectable draft")
    result = client.say(session, "Yes, send me the summary.", turn_id="send-summary")
    check(result["state"]["status"] == "completed" and result["state"]["email_status"] == "simulated_sent", "explicit consent creates a simulated send")
    replay = client.say(session, "Yes, send me the summary.", turn_id="send-summary")
    check(replay == result, "email turn retries are idempotent")

    skip_session = client.session()
    result = client.say(skip_session, "My name is Margaret Chen. I'm calling about my denied healthcare claim from January, but I don't want to share my SSN.")
    check(result["state"]["phase"] == "VERIFY_ID" and result["state"]["case_hints"].get("month") == 1,
          "partial identity and SSN refusal preserve later case hints")
    result = client.say(skip_session, "I already told you who I am. This is ridiculous. Just tell me why my claim was denied.")
    check(not result["state"]["verified"] and "pathology" not in result["reply"].lower(),
          "frustration does not bypass verification or disclose claim facts")
    people = json.loads((ROOT / "fixtures" / "policyholders.json").read_text())
    person = next(person for person in people if person["name"] == "Margaret Chen")
    result = client.say(skip_session, f"My DOB is {person['dob']} and my email is {person['email']}.")
    check(result["state"]["verified"] and result["state"]["selected_case_id"] == "CL-2048",
          "alternative identity fields complete verification using remembered hints")
    client.say(skip_session, "That's all.")
    result = client.say(skip_session, "No thanks, skip the email.")
    check(result["state"]["email_status"] == "skipped" and result["state"]["status"] == "completed", "skip completes without sending")
    client.request("GET", f"/api/sessions/{session['session_id']}", token=skip_session["access_token"], expected=404)
    print("PASS  another session token cannot read this session")

    failure_session = client.session()
    result = client.say(failure_session, "My name is Margaret Chen. DOB is 1985-03-15. SSN last four is 9999. Tell me why my healthcare claim was denied.")
    check(not result["state"]["verified"] and result["state"]["phase"] == "VERIFY_ID", "incorrect identity cannot advance")
    check(result["state"]["selected_case_id"] is None and "pathology" not in result["reply"].lower(), "unverified response does not disclose recorded claim details")
    for attempt in range(3):
        result = client.say(failure_session, "What is RL?")
    check(result["state"]["pending"] == "human_offer", "repeated unrelated questions offer simulated human support")
    if multilingual:
        multilingual_scenario(client)
    print("\nReal-model HTTP smoke test passed. Email and human handoff remain simulated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--multilingual", action="store_true", help="Add a Spanish workflow and print replies for language review; uses additional model requests.")
    args = parser.parse_args()
    try:
        run(args.base_url, args.multilingual)
    except (AssertionError, RuntimeError, ConfigurationError, KeyError, ValueError) as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        sys.exit(1)
