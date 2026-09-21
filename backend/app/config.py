import os
from datetime import date
from pathlib import Path
from dotenv import load_dotenv

PROJECT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT / ".env")


def settings():
    return {
        "database": os.getenv("DATABASE_PATH", str(PROJECT / "data" / "insurance.db")),
        "fixtures": Path(os.getenv("FIXTURES_PATH", str(PROJECT / "fixtures"))),
        "static": Path(os.getenv("STATIC_PATH", str(PROJECT / "frontend" / "dist"))),
        "api_key": os.getenv("MODEL_API_KEY", ""),
        "base_url": os.getenv("MODEL_BASE_URL", "https://api.openai.com/v1"),
        "model": os.getenv("MODEL_NAME", ""),
        "demo_date": os.getenv("DEMO_DATE", "") or date.today().isoformat(),
        "secret_ttl": 3600,
        "email_failure": os.getenv("SIMULATE_EMAIL_FAILURE", "false").lower() == "true",
    }
