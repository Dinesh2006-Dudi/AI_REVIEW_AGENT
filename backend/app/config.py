"""Configuration read from environment variables (optionally from backend/.env)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]


def load_env(path=None):
    """Tiny .env loader so no extra dependency is needed. Real env vars win."""
    p = Path(path) if path else BASE_DIR / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        k, v = key.strip(), value.strip().strip('"').strip("'")
        if not os.environ.get(k):   # also overrides variables that exist but are empty
            os.environ[k] = v


class Settings:
    def __init__(self):
        load_env()
        self.db_path = os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "app.db"))
        self.dashboard_mode = os.getenv("DASHBOARD_MODE", "mock").lower()  # mock | organizer
        self.dashboard_db_path = os.getenv("MOCK_DASHBOARD_DB_PATH", str(BASE_DIR / "data" / "mock_dashboard.db"))
        self.ai_provider = os.getenv("AI_PROVIDER", "gemini").lower()  # claude | mock
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
