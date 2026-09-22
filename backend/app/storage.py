"""Durable snapshots and idempotency receipts. Raw API keys/PII never go here."""
import hashlib
import hmac
import json
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, snapshot TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS turns (
                    session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    message_hash TEXT NOT NULL, response TEXT NOT NULL,
                    PRIMARY KEY (session_id, turn_id)
                );
                CREATE TABLE IF NOT EXISTS email_outbox (
                    session_id TEXT NOT NULL, version INTEGER NOT NULL,
                    summary TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY (session_id, version)
                );
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=15)

    @staticmethod
    def digest(text: str):
        return hashlib.sha256(text.encode()).hexdigest()

    def create(self, session_id: str, token: str, snapshot: dict):
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (session_id, self.digest(token), json.dumps(snapshot)))

    def load(self, session_id: str, token: str):
        with self.connect() as db:
            row = db.execute("SELECT token_hash, snapshot FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row or not hmac.compare_digest(row[0], self.digest(token)):
            return None
        return json.loads(row[1])

    def receipt(self, session_id: str, turn_id: str, message: str):
        with self.connect() as db:
            row = db.execute("SELECT message_hash, response FROM turns WHERE session_id=? AND turn_id=?", (session_id, turn_id)).fetchone()
        if not row:
            return None
        if not hmac.compare_digest(row[0], self.digest(message)):
            raise ValueError("This turn_id was already used with a different message.")
        return json.loads(row[1])

    def save(self, session_id: str, snapshot: dict, turn_id=None, message=None, response=None):
        with self.connect() as db:
            updated = db.execute("UPDATE sessions SET snapshot=? WHERE id=?", (json.dumps(snapshot), session_id))
            if updated.rowcount != 1:
                raise ValueError("Session not found")
            if turn_id is not None:
                db.execute("INSERT INTO turns VALUES (?, ?, ?, ?)", (session_id, turn_id, self.digest(message), json.dumps(response)))
            summary = snapshot["email_summary"]
            if summary and summary["status"] == "simulated_sent":
                db.execute("INSERT OR IGNORE INTO email_outbox VALUES (?, ?, ?, ?)", (session_id, summary["version"], json.dumps(summary), "simulated_sent"))
