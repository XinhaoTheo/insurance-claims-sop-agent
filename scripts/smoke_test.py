#!/usr/bin/env python3
"""Exercise the local HTTP contract with stdlib only; no model key is needed.

Run against an already running app:
    python3 scripts/smoke_test.py --base-url http://127.0.0.1:8000
Creates small synthetic test sessions in that instance's SQLite database.
"""

import argparse
import json
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SAMPLE = (
    "I’m the policyholder. My name is Margaret Chen, policy POL-9921. "
    "I’m calling about my denied healthcare claim from January. "
    "DOB is 1985-03-15, SSN last four is 4472."
)


class Client:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")

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
            with urlopen(request, timeout=20) as response:
                status, data = response.status, json.load(response)
        except HTTPError as exc:
            status = exc.code
            try:
                data = json.load(exc)
            except (ValueError, TypeError):
                data = {}
        except (URLError, TimeoutError):
            raise RuntimeError("Cannot reach the app. Start it first and check --base-url.") from None
        if status != expected:
            # Do not dump full session bodies or tokens on error.
            raise AssertionError(f"{method} {path}: expected HTTP {expected}, got {status}")
        return data

    def session(self):
        return self.request("POST", "/api/sessions", {
            "mode": "offline", "demo_date": "2026-03-10",
        }, expected=201)

    def say(self, session, text, turn_id=None, expected=200):
        return self.request(
            "POST", f"/api/sessions/{session['session_id']}/messages",
            {"message": text, "turn_id": turn_id or str(uuid.uuid4())},
            token=session["access_token"], expected=expected,
        )


def check(condition, label):
    if not condition:
        raise AssertionError(label)
    print("PASS  " + label)


def run(base_url):
    client = Client(base_url)
    health = client.request("GET", "/health")
    check(health["status"] == "ok" and health["email_mode"] == "mock", "health and explicit mock email mode")

    session = client.session()
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

    result = client.say(session, "That's all, no more questions.")
    check(result["state"]["phase"] == "POST_PROCESS" and result["state"]["email_status"] == "awaiting_choice", "post-process waits for email consent")
    check(bool(result["email_summary"]["body"]), "summary includes an inspectable draft")
    result = client.say(session, "Yes, send me the summary.", turn_id="send-summary")
    check(result["state"]["status"] == "completed" and result["state"]["email_status"] == "simulated_sent", "explicit consent creates a simulated send")
    replay = client.say(session, "Yes, send me the summary.", turn_id="send-summary")
    check(replay == result, "email turn retries are idempotent")

    skip_session = client.session()
    client.say(skip_session, SAMPLE)
    client.say(skip_session, "That's all.")
    result = client.say(skip_session, "No thanks, skip the email.")
    check(result["state"]["email_status"] == "skipped" and result["state"]["status"] == "completed", "skip completes without sending")
    client.request("GET", f"/api/sessions/{session['session_id']}", token=skip_session["access_token"], expected=404)
    print("PASS  another session token cannot read this session")

    failure_session = client.session()
    result = client.say(failure_session, "My name is Margaret Chen. DOB is 1985-03-15. SSN last four is 9999. Tell me why my healthcare claim was denied.")
    check(not result["state"]["verified"] and result["state"]["phase"] == "VERIFY_ID", "incorrect identity cannot advance")
    check(result["state"]["selected_case_id"] is None and "pathology" not in result["reply"].lower(), "unverified response does not disclose recorded claim details")
    for _ in range(3):
        result = client.say(failure_session, "What is RL?")
    check(result["state"]["pending"] == "human_offer", "repeated unrelated questions offer simulated human support")
    print("\nHTTP smoke test passed. Live-model quality is evaluated separately with your API key.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    try:
        run(args.base_url)
    except (AssertionError, RuntimeError, KeyError, ValueError) as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        sys.exit(1)
