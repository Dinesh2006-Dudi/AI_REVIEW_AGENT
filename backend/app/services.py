"""Core workflow: sync, draft, approve, post (idempotent), verify, results.

Every public method holds one re-entrant lock, so a double-click or retry can never run two posts at once.
The backend (not the UI) enforces: only the exact, currently approved draft version can reach the dashboard.
"""
import functools
import re
import threading

from . import safety
from .dashboard import DashboardConflict, DashboardError, DashboardNotFound, DashboardTimeout
from .db import STATUSES, init_schema, now, tx
from .errors import Conflict, Invalid, NotFound, ServiceError, Unavailable
from .generator import MockGenerator, ReplyRequest

ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
BLOCKED = ("replied", "already_replied", "verification_pending")  # no edits/decisions while in these states
MAX_REPLY_CHARS = 1500
DECISIONS = {"approve": "approved", "reject": "rejected", "skip": "skipped", "draft_only": "draft_only"}
DECISION_STATUS = {"approved": "approved", "rejected": "rejected", "skipped": "skipped", "draft_only": "draft_only"}


def locked(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self.lock:
            return fn(self, *args, **kwargs)
    return wrapper


class ReviewService:
    def __init__(self, conn, dashboard, generator):
        self.conn, self.dashboard, self.generator = conn, dashboard, generator
        self.lock = threading.RLock()
        init_schema(conn)

    # ---------- small helpers ----------
    def _one(self, sql, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _all(self, sql, params=()):
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def _review(self, review_id):
        if not isinstance(review_id, str) or not ID_RE.match(review_id):
            raise Invalid("Invalid review ID format.")
        row = self._one("SELECT * FROM reviews WHERE id = ?", (review_id,))
        if not row:
            raise NotFound(f"Review {review_id} was not found.")
        return row

    def _set_status(self, review_id, new_status, note=None):
        old = self._one("SELECT status FROM reviews WHERE id = ?", (review_id,))["status"]
        ts = now()
        self.conn.execute(
            "UPDATE reviews SET status = ?, status_reason = ?, updated_at = ? WHERE id = ?",
            (new_status, note, ts, review_id),
        )
        self.conn.execute(
            "INSERT INTO status_history (review_id, old_status, new_status, note, created_at) VALUES (?,?,?,?,?)",
            (review_id, old, new_status, note, ts),
        )

    def _latest_draft(self, review_id):
        return self._one("SELECT * FROM reply_drafts WHERE review_id = ? ORDER BY version DESC LIMIT 1", (review_id,))

    def _active_decision(self, review_id):
        return self._one("SELECT * FROM approval_decisions WHERE review_id = ? AND invalidated_at IS NULL", (review_id,))

    def _invalidate_decisions(self, review_id, reason):
        self.conn.execute(
            "UPDATE approval_decisions SET invalidated_at = ?, invalidated_reason = ? "
            "WHERE review_id = ? AND invalidated_at IS NULL",
            (now(), reason, review_id),
        )

    def _add_draft(self, review_id, text, source, tone, flagged, reason):
        version = self._one("SELECT COALESCE(MAX(version), 0) + 1 AS v FROM reply_drafts WHERE review_id = ?", (review_id,))["v"]
        cur = self.conn.execute(
            "INSERT INTO reply_drafts (review_id, version, text, source, tone, flagged, flag_reason, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (review_id, version, text, source, tone, 1 if flagged else 0, reason, now()),
        )
        return self._one("SELECT * FROM reply_drafts WHERE id = ?", (cur.lastrowid,))

    def _ensure_editable(self, review):
        if review["status"] in BLOCKED:
            raise Conflict(f"Review {review['id']} is '{review['status']}' and can no longer be changed here.")

    # ---------- settings ----------
    @locked
    def get_settings(self):
        return self._one("SELECT tone, extra_instructions, require_approval, updated_at FROM owner_settings WHERE id = 1")

    @locked
    def update_settings(self, tone, extra_instructions=""):
        tone = (tone or "").strip()
        extra_instructions = (extra_instructions or "").strip()
        if not tone or len(tone) > 40:
            raise Invalid("Tone must be 1-40 characters.")
        if len(extra_instructions) > 500:
            raise Invalid("Extra instructions must be at most 500 characters.")
        # require_approval is always 1 (also enforced by a CHECK constraint).
        with tx(self.conn):
            self.conn.execute(
                "UPDATE owner_settings SET tone = ?, extra_instructions = ?, updated_at = ? WHERE id = 1",
                (tone, extra_instructions, now()),
            )
        return self.get_settings()

    # ---------- reviews ----------
    @locked
    def sync_reviews(self):
        try:
            remote = self.dashboard.list_reviews()
        except DashboardError as exc:
            raise Unavailable(f"Could not load reviews from the dashboard: {exc}")
        added = 0
        with tx(self.conn):
            for r in remote:
                rid = str(r.get("id", ""))
                if not ID_RE.match(rid):
                    continue  # skip anything without a valid stable ID
                reply = (r.get("reply") or {}).get("text")
                row = self._one("SELECT status FROM reviews WHERE id = ?", (rid,))
                ts = now()
                if row is None:
                    self.conn.execute(
                        "INSERT INTO reviews (id, reviewer_name, rating, text, existing_reply, review_date, status, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,'new',?,?)",
                        (rid, str(r["reviewer_name"])[:100], int(r["rating"]), str(r["text"]), reply, r.get("date"), ts, ts),
                    )
                    self.conn.execute(
                        "INSERT INTO status_history (review_id, old_status, new_status, note, created_at) VALUES (?,NULL,'new','imported',?)",
                        (rid, ts),
                    )
                    if reply:
                        self._set_status(rid, "already_replied", "Dashboard already shows a reply")
                    added += 1
                else:
                    self.conn.execute("UPDATE reviews SET existing_reply = ? WHERE id = ?", (reply, rid))
                    if reply and row["status"] in ("new", "drafted", "draft_only", "needs_attention", "approved", "rejected", "skipped"):
                        self._set_status(rid, "already_replied", "Dashboard already shows a reply")
        return {"added": added, "total": len(remote)}

    def _filters(self, status, rating, review_id):
        where, params = [], []
        if status:
            if status not in STATUSES:
                raise Invalid(f"Unknown status '{status}'.")
            where.append("r.status = ?")
            params.append(status)
        if rating is not None:
            if not 1 <= int(rating) <= 5:
                raise Invalid("Rating must be between 1 and 5.")
            where.append("r.rating = ?")
            params.append(int(rating))
        if review_id:
            if not ID_RE.match(review_id):
                raise Invalid("Invalid review ID filter.")
            where.append("instr(r.id, ?) > 0")
            params.append(review_id)
        return (" WHERE " + " AND ".join(where)) if where else "", params

    def _decorate(self, review):
        rid = review["id"]
        draft = self._latest_draft(rid)
        review["latest_draft"] = draft
        review["decision"] = self._active_decision(rid)
        review["attempt"] = self._one("SELECT * FROM posting_attempts WHERE review_id = ? ORDER BY id DESC LIMIT 1", (rid,))
        review["verification"] = self._one("SELECT * FROM verifications WHERE review_id = ? ORDER BY id DESC LIMIT 1", (rid,))
        return review

    @locked
    def list_reviews(self, status=None, rating=None, review_id=None):
        where, params = self._filters(status, rating, review_id)
        rows = self._all(f"SELECT r.* FROM reviews r{where} ORDER BY r.id", params)
        return [self._decorate(r) for r in rows]

    @locked
    def get_review_detail(self, review_id):
        review = self._decorate(self._review(review_id))
        review["drafts"] = self._all("SELECT * FROM reply_drafts WHERE review_id = ? ORDER BY version", (review_id,))
        review["decisions"] = self._all("SELECT * FROM approval_decisions WHERE review_id = ? ORDER BY id", (review_id,))
        review["history"] = self._all("SELECT * FROM status_history WHERE review_id = ? ORDER BY id", (review_id,))
        return review

    @locked
    def summary(self):
        counts = {s: 0 for s in STATUSES}
        for row in self._all("SELECT status, COUNT(*) AS n FROM reviews GROUP BY status"):
            counts[row["status"]] = row["n"]
        return {"total": sum(counts.values()), "counts": counts}

    # ---------- drafting ----------
    @locked
    def generate_draft(self, review_id):
        review = self._review(review_id)
        self._ensure_editable(review)
        settings = self.get_settings()
        reasons = safety.analyze_review(review["text"])
        clean_text, found = safety.redact(review["text"])
        if found:
            reasons.append("Review contains private contact details (removed before the AI call)")
        req = ReplyRequest(
            review_id=review_id,
            first_name=safety.first_name(review["reviewer_name"]),
            rating=review["rating"],
            text=clean_text,
            tone=settings["tone"],
            extra_instructions=settings["extra_instructions"],
        )
        try:
            reply = self.generator.generate(req)
        except ServiceError:
            raise
        except Exception as exc:
            raise Unavailable(f"Reply generation failed ({type(exc).__name__}).")
        reply = (reply or "").strip()
        if not reply:
            raise Unavailable("The AI returned an empty reply.")
        problems = safety.check_reply(reply)
        if problems["private"]:
            reply = MockGenerator().generate(req)
            reasons.append("The AI reply contained private details and was replaced with a safe template")
        if problems["promises"]:
            reasons.append("Reply may mention a refund/compensation; confirm it is authorized or edit it out")
        reply = reply[:MAX_REPLY_CHARS].rstrip()
        with tx(self.conn):
            self._invalidate_decisions(review_id, "new draft generated")
            draft = self._add_draft(review_id, reply, "ai", settings["tone"], bool(reasons), "; ".join(reasons) or None)
            self._set_status(review_id, "needs_attention" if reasons else "drafted", "; ".join(reasons) or None)
        return draft

    @locked
    def edit_draft(self, review_id, text, base_version):
        review = self._review(review_id)
        self._ensure_editable(review)
        text = (text or "").strip()
        if not text:
            raise Invalid("Reply text cannot be empty.")
        if len(text) > MAX_REPLY_CHARS:
            raise Invalid(f"Reply text must be at most {MAX_REPLY_CHARS} characters.")
        latest = self._latest_draft(review_id)
        current = latest["version"] if latest else 0
        if base_version != current:
            raise Conflict("This draft changed since you loaded it. Reload and try again.")
        if latest and latest["text"] == text:
            return latest
        problems = safety.check_reply(text)
        reasons = []
        if problems["private"]:
            reasons.append("Draft contains an email address or phone number")
        if problems["promises"]:
            reasons.append("Draft mentions a refund/compensation")
        with tx(self.conn):
            self._invalidate_decisions(review_id, "draft edited; fresh approval required")
            draft = self._add_draft(review_id, text, "owner", None, bool(reasons), "; ".join(reasons) or None)
            self._set_status(review_id, "drafted", "Edited by owner; needs approval")
        return draft

    # ---------- decisions ----------
    @locked
    def decide(self, review_id, decision, version=None, reason=None):
        decision = DECISIONS.get(decision, decision)
        if decision not in DECISION_STATUS:
            raise Invalid("Unknown decision.")
        review = self._review(review_id)
        self._ensure_editable(review)
        reason = (reason or "").strip()[:300] or None
        latest = self._latest_draft(review_id)
        if decision != "skipped":
            if latest is None:
                raise Conflict("There is no draft for this review yet.")
            if version != latest["version"]:
                raise Conflict("The draft changed. Reload to see the latest version before deciding.")
        draft_id = latest["id"] if latest else None
        active = self._active_decision(review_id)
        if active and active["decision"] == decision and active["draft_id"] == draft_id:
            return active  # repeated click: nothing new to record
        with tx(self.conn):
            self._invalidate_decisions(review_id, f"superseded by '{decision}'")
            self.conn.execute(
                "INSERT INTO approval_decisions (review_id, draft_id, decision, reason, created_at) VALUES (?,?,?,?,?)",
                (review_id, draft_id, decision, reason, now()),
            )
            self._set_status(review_id, DECISION_STATUS[decision], reason)
        return self._active_decision(review_id)

    @locked
    def approve_and_post(self, review_id, version):
        review = self._review(review_id)
        latest = self._latest_draft(review_id)
        if review["status"] in ("replied", "verification_pending") and latest and latest["version"] == version:
            attempt = self._one("SELECT status FROM posting_attempts WHERE draft_id = ?", (latest["id"],))
            if attempt and attempt["status"] in ("posted", "unknown", "pending"):
                return self.post_approved(review_id)  # repeated click / retry: re-checks, never re-posts blindly
        self.decide(review_id, "approved", version)
        return self.post_approved(review_id)

    # ---------- posting ----------
    @locked
    def post_approved(self, review_id):
        review = self._review(review_id)
        if review["status"] == "already_replied":
            raise Conflict("This review already has a reply on the dashboard; nothing was posted.")
        draft = self._latest_draft(review_id)
        if draft is None:
            raise Conflict("There is no draft for this review.")
        decision = self._active_decision(review_id)
        if not decision or decision["decision"] != "approved" or decision["draft_id"] != draft["id"]:
            raise Conflict("The current draft version has not been approved, so it cannot be posted.")
        key = f"{review_id}:draft{draft['id']}"
        attempt = self._one("SELECT * FROM posting_attempts WHERE idempotency_key = ?", (key,))
        if attempt and attempt["status"] == "posted":
            if review["status"] == "replied":
                return self._result("verified", "Already posted and verified; nothing new was sent.", attempt["id"])
            return self._verify(attempt["id"])

        # Always look at the dashboard first: the review must exist and must not already have a reply.
        try:
            remote = self.dashboard.get_review(review_id)
        except DashboardNotFound:
            raise Conflict(f"Review {review_id} does not exist on the dashboard; nothing was posted.")
        except DashboardError as exc:
            raise Unavailable(f"Could not reach the dashboard, so nothing was posted. ({exc})")
        reply = remote.get("reply")
        if reply:
            if attempt and reply["review_id"] == review_id and reply["text"] == draft["text"]:
                # An earlier uncertain attempt actually landed.
                self._mark_posted(attempt["id"], reply["id"])
                return self._verify(attempt["id"])
            with tx(self.conn):
                self.conn.execute("UPDATE reviews SET existing_reply = ? WHERE id = ?", (reply["text"], review_id))
                self._set_status(review_id, "already_replied", "Dashboard already shows a reply")
                if attempt:
                    self.conn.execute(
                        "UPDATE posting_attempts SET status='failed', error=?, updated_at=? WHERE id=?",
                        ("Dashboard already had a different reply", now(), attempt["id"]),
                    )
            raise Conflict("This review already has a reply on the dashboard; nothing was posted.")

        with tx(self.conn):
            if attempt is None:
                cur = self.conn.execute(
                    "INSERT INTO posting_attempts (review_id, draft_id, idempotency_key, posted_text, status, created_at, updated_at) "
                    "VALUES (?,?,?,?,'pending',?,?)",
                    (review_id, draft["id"], key, draft["text"], now(), now()),
                )
                attempt_id = cur.lastrowid
            else:
                attempt_id = attempt["id"]
                self.conn.execute(
                    "UPDATE posting_attempts SET status='pending', error=NULL, attempt_count=attempt_count+1, updated_at=? WHERE id=?",
                    (now(), attempt_id),
                )
        try:
            posted = self.dashboard.post_reply(review_id, draft["text"], key)
        except DashboardTimeout:
            return self._after_timeout(review_id, draft, attempt_id)
        except DashboardError as exc:
            return self._fail(review_id, attempt_id, f"The dashboard rejected the post: {exc}")
        except Exception:
            return self._after_timeout(review_id, draft, attempt_id)
        self._mark_posted(attempt_id, (posted or {}).get("id"))
        return self._verify(attempt_id)

    def _mark_posted(self, attempt_id, reply_id):
        attempt = self._one("SELECT review_id FROM posting_attempts WHERE id = ?", (attempt_id,))
        with tx(self.conn):
            self.conn.execute(
                "UPDATE posting_attempts SET status='posted', dashboard_reply_id=?, error=NULL, updated_at=? WHERE id=?",
                (reply_id, now(), attempt_id),
            )
            self._set_status(attempt["review_id"], "verification_pending", "Posted; awaiting verification")

    def _fail(self, review_id, attempt_id, message):
        with tx(self.conn):
            self.conn.execute(
                "UPDATE posting_attempts SET status='failed', error=?, updated_at=? WHERE id=?",
                (message[:300], now(), attempt_id),
            )
            self._set_status(review_id, "post_failed", message[:300])
        return self._result("failed", message, attempt_id)

    def _after_timeout(self, review_id, draft, attempt_id):
        """A timeout does not mean failure: look at the dashboard before deciding anything."""
        try:
            remote = self.dashboard.get_review(review_id)
        except DashboardError:
            msg = "The dashboard timed out and its state could not be checked. Use Verify before retrying."
            with tx(self.conn):
                self.conn.execute(
                    "UPDATE posting_attempts SET status='unknown', error=?, updated_at=? WHERE id=?", (msg, now(), attempt_id)
                )
                self._set_status(review_id, "verification_pending", msg)
            return self._result("unknown", msg, attempt_id)
        reply = remote.get("reply")
        if reply and reply["review_id"] == review_id and reply["text"] == draft["text"]:
            self._mark_posted(attempt_id, reply["id"])
            return self._verify(attempt_id)
        if reply:
            with tx(self.conn):
                self.conn.execute("UPDATE reviews SET existing_reply = ? WHERE id = ?", (reply["text"], review_id))
                self._set_status(review_id, "already_replied", "Dashboard already shows a different reply")
                self.conn.execute(
                    "UPDATE posting_attempts SET status='failed', error=?, updated_at=? WHERE id=?",
                    ("Dashboard has a different reply", now(), attempt_id),
                )
            return self._result("failed", "The dashboard already has a different reply for this review.", attempt_id)
        return self._fail(
            review_id, attempt_id,
            "The request timed out; the dashboard confirms no reply was posted. It is safe to retry.",
        )

    # ---------- verification ----------
    def _verify(self, attempt_id):
        attempt = self._one("SELECT * FROM posting_attempts WHERE id = ?", (attempt_id,))
        review_id = attempt["review_id"]
        observed_text = observed_id = None
        try:
            remote = self.dashboard.get_review(review_id)  # fresh reload from the dashboard
        except DashboardError as exc:
            ok, detail = False, f"Could not reload the review from the dashboard: {exc}"
        else:
            problems = []
            reply = remote.get("reply")
            if remote.get("id") != review_id:
                problems.append("the dashboard returned a different review ID")
            if not reply:
                problems.append("no reply is visible on the review")
            else:
                observed_text, observed_id = reply.get("text"), reply.get("id")
                if reply.get("review_id") != review_id:
                    problems.append("the reply belongs to a different review ID")
                if observed_text != attempt["posted_text"]:
                    problems.append("the visible reply text differs from the approved text")
                if attempt["dashboard_reply_id"] and observed_id != attempt["dashboard_reply_id"]:
                    problems.append("the reply ID differs from the one returned when posting")
            ok = not problems
            detail = "All checks passed." if ok else "Verification failed: " + "; ".join(problems) + "."
        with tx(self.conn):
            self.conn.execute(
                "INSERT INTO verifications (attempt_id, review_id, draft_id, verified, observed_reply_text, observed_reply_id, detail, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (attempt_id, review_id, attempt["draft_id"], 1 if ok else 0, observed_text, observed_id, detail, now()),
            )
            self._set_status(review_id, "replied" if ok else "verification_pending", detail)
        if ok:
            return self._result("verified", "Reply posted and verified on the dashboard.", attempt_id)
        return self._result("verification_pending", detail, attempt_id)

    @locked
    def verify_review(self, review_id):
        self._review(review_id)
        attempt = self._one("SELECT * FROM posting_attempts WHERE review_id = ? ORDER BY id DESC LIMIT 1", (review_id,))
        if not attempt:
            raise Conflict("Nothing has been posted for this review yet.")
        if attempt["status"] == "posted":
            return self._verify(attempt["id"])
        if attempt["status"] in ("unknown", "pending"):
            return self.post_approved(review_id)  # checks the dashboard first, only posts if nothing is there
        raise Conflict("The last posting attempt failed; use Approve and Post to retry safely.")

    def _result(self, outcome, message, attempt_id):
        attempt = self._one("SELECT review_id FROM posting_attempts WHERE id = ?", (attempt_id,))
        return {"outcome": outcome, "message": message, "review": self.get_review_detail(attempt["review_id"])}

    # ---------- results ----------
    @locked
    def results(self, status=None, rating=None, review_id=None):
        rows = self.list_reviews(status, rating, review_id)
        pick = lambda *statuses: [r for r in rows if r["status"] in statuses]
        return {
            "replied": pick("replied"),
            "drafted_only": pick("drafted", "draft_only", "approved"),
            "skipped": pick("skipped", "rejected", "already_replied", "needs_attention"),
            "failed": pick("post_failed"),
            "verification_pending": pick("verification_pending"),
        }
