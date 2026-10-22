"""Dashboard adapters.

The service layer only talks to the DashboardAdapter interface. MockDashboard is a local stand-in for
the organizer's mock review dashboard. OrganizerDashboard is a placeholder: its real endpoints/auth are
NOT known yet, so it deliberately raises instead of guessing.
"""
import threading
import uuid
from abc import ABC, abstractmethod

from .db import connect, now


class DashboardError(Exception):
    pass


class DashboardTimeout(DashboardError):
    pass


class DashboardNotFound(DashboardError):
    pass


class DashboardConflict(DashboardError):
    pass


class DashboardAdapter(ABC):
    """Review shape: {id, reviewer_name, rating, text, date, reply: None | {id, review_id, text, created_at}}"""

    @abstractmethod
    def list_reviews(self) -> list: ...

    @abstractmethod
    def get_review(self, review_id: str) -> dict: ...

    @abstractmethod
    def post_reply(self, review_id: str, text: str, idempotency_key: str) -> dict: ...


class OrganizerDashboard(DashboardAdapter):
    """TODO: implement once the organizer's dashboard documentation/credentials are available."""

    MSG = ("The organizer dashboard interface is not configured. Its documented endpoints and permitted "
           "access are required; use DASHBOARD_MODE=mock until then.")

    def list_reviews(self):
        raise DashboardError(self.MSG)

    def get_review(self, review_id):
        raise DashboardError(self.MSG)

    def post_reply(self, review_id, text, idempotency_key):
        raise DashboardError(self.MSG)


class MockDashboard(DashboardAdapter):
    """SQLite-backed fake dashboard with failure injection for tests.

    next_post_behavior: None | 'error' | 'timeout_before' (reply NOT stored) |
                        'timeout_after' (reply stored but the call raises a timeout) | 'tamper' (stores altered text)
    fail_next_get: when True the next get_review raises DashboardError.
    """

    def __init__(self, path: str = ":memory:"):
        self.conn = connect(path)
        self.lock = threading.RLock()
        self.post_calls = []
        self.next_post_behavior = None
        self.fail_next_get = False
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS mock_reviews (
                id TEXT PRIMARY KEY, reviewer_name TEXT NOT NULL, rating INTEGER NOT NULL,
                text TEXT NOT NULL, review_date TEXT);
            CREATE TABLE IF NOT EXISTS mock_replies (
                id TEXT PRIMARY KEY, review_id TEXT NOT NULL UNIQUE REFERENCES mock_reviews(id),
                text TEXT NOT NULL, idempotency_key TEXT UNIQUE, created_at TEXT NOT NULL);
            """
        )

    @staticmethod
    def _rid() -> str:
        return "rep_" + uuid.uuid4().hex[:8]

    def is_empty(self) -> bool:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM mock_reviews").fetchone()[0] == 0

    def seed(self, reviews: list) -> None:
        with self.lock:
            for r in reviews:
                self.conn.execute(
                    "INSERT OR IGNORE INTO mock_reviews (id, reviewer_name, rating, text, review_date) VALUES (?,?,?,?,?)",
                    (r["id"], r["reviewer_name"], r["rating"], r["text"], r.get("date")),
                )
                if r.get("existing_reply"):
                    self.add_external_reply(r["id"], r["existing_reply"])

    def add_external_reply(self, review_id: str, text: str) -> None:
        """Simulates someone else replying directly on the dashboard."""
        with self.lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO mock_replies (id, review_id, text, idempotency_key, created_at) VALUES (?,?,?,?,?)",
                (self._rid(), review_id, text, None, now()),
            )

    def _reply_for(self, review_id: str):
        row = self.conn.execute(
            "SELECT id, review_id, text, created_at FROM mock_replies WHERE review_id = ?", (review_id,)
        ).fetchone()
        return dict(row) if row else None

    def _review_dict(self, row) -> dict:
        d = dict(row)
        d["date"] = d.pop("review_date")
        d["reply"] = self._reply_for(d["id"])
        return d

    def list_reviews(self) -> list:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM mock_reviews ORDER BY id").fetchall()
            return [self._review_dict(r) for r in rows]

    def get_review(self, review_id: str) -> dict:
        with self.lock:
            if self.fail_next_get:
                self.fail_next_get = False
                raise DashboardError("simulated dashboard outage")
            row = self.conn.execute("SELECT * FROM mock_reviews WHERE id = ?", (review_id,)).fetchone()
            if not row:
                raise DashboardNotFound(f"review {review_id} not found")
            return self._review_dict(row)

    def post_reply(self, review_id: str, text: str, idempotency_key: str) -> dict:
        with self.lock:
            self.post_calls.append({"review_id": review_id, "text": text, "key": idempotency_key})
            behavior, self.next_post_behavior = self.next_post_behavior, None
            if behavior == "timeout_before":
                raise DashboardTimeout("simulated timeout before the request was processed")
            if behavior == "error":
                raise DashboardError("simulated 500 error")
            if not self.conn.execute("SELECT 1 FROM mock_reviews WHERE id = ?", (review_id,)).fetchone():
                raise DashboardNotFound(f"review {review_id} not found")
            existing = self._reply_for(review_id)
            if existing:
                same = self.conn.execute(
                    "SELECT 1 FROM mock_replies WHERE review_id = ? AND idempotency_key = ?", (review_id, idempotency_key)
                ).fetchone()
                if same:
                    return existing
                raise DashboardConflict("review already has a reply")
            stored = text + " [edited by dashboard]" if behavior == "tamper" else text
            rid = self._rid()
            self.conn.execute(
                "INSERT INTO mock_replies (id, review_id, text, idempotency_key, created_at) VALUES (?,?,?,?,?)",
                (rid, review_id, stored, idempotency_key, now()),
            )
            reply = self._reply_for(review_id)
            if behavior == "timeout_after":
                raise DashboardTimeout("simulated timeout after the reply was stored")
            return reply
