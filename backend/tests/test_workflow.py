"""Workflow tests (unittest style, so they run with pytest OR `python -m unittest`).
They use MockDashboard and MockGenerator: no network, no live AI calls."""
import sqlite3
import unittest
from types import SimpleNamespace

from app import safety
from app.dashboard import MockDashboard
from app.db import connect
from app.errors import Conflict
from app.generator import ClaudeGenerator, MockGenerator, ReplyRequest
from app.sample_data import SAMPLE_REVIEWS
from app.services import ReviewService


class CapturingGenerator(MockGenerator):
    def __init__(self):
        self.requests = []

    def generate(self, req):
        self.requests.append(req)
        return super().generate(req)


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        self.dash = MockDashboard(":memory:")
        self.dash.seed(SAMPLE_REVIEWS)
        self.gen = CapturingGenerator()
        self.svc = ReviewService(self.conn, self.dash, self.gen)
        self.svc.sync_reviews()

    def draft(self, rid):
        return self.svc.generate_draft(rid)

    def status(self, rid):
        return self.svc.get_review_detail(rid)["status"]


class TestGeneration(Base):
    def test_01_positive_review_gets_appropriate_reply(self):
        text = self.draft("rev_1001")["text"]
        self.assertIn("Thank", text)
        self.assertIn("food", text)
        self.assertIn("Priya", text)

    def test_02_negative_review_gets_polite_relevant_reply(self):
        text = self.draft("rev_1002")["text"].lower()
        self.assertIn("sorry", text)
        self.assertIn("wait", text)
        for bad in ("refund", "free", "discount", "compensat", "your fault"):
            self.assertNotIn(bad, text)

    def test_03_tone_changes_reply(self):
        outputs = {}
        for tone in ("friendly", "professional", "casual"):
            self.svc.update_settings(tone)
            outputs[tone] = self.draft("rev_1001")["text"]
        self.assertEqual(len(set(outputs.values())), 3)
        self.assertTrue(outputs["friendly"].startswith("Hi"))
        self.assertTrue(outputs["professional"].startswith("Dear"))
        self.assertTrue(outputs["casual"].startswith("Hey"))

    def test_04_private_info_excluded(self):
        d = self.draft("rev_1004")
        self.assertNotIn("maria.lopez@example.com", d["text"])
        self.assertNotIn("555-123-4567", d["text"])
        sent = self.gen.requests[-1].text
        self.assertNotIn("maria.lopez@example.com", sent)
        self.assertNotIn("555-123-4567", sent)
        self.assertTrue(d["flagged"])

    def test_04b_ai_reply_that_leaks_private_data_is_replaced(self):
        class Leaky:
            def generate(self, req):
                return "Thanks! Email us at owner@example.com or call 555-987-6543."
        svc = ReviewService(self.conn, self.dash, Leaky())
        d = svc.generate_draft("rev_1001")
        self.assertNotIn("@", d["text"])
        self.assertNotIn("555", d["text"])
        self.assertTrue(d["flagged"])

    def test_04c_sensitive_and_injection_reviews_are_flagged(self):
        self.assertEqual(self.draft("rev_1008")["flagged"], 1)
        d = self.draft("rev_1006")
        self.assertEqual(d["flagged"], 1)
        self.assertNotIn("free", d["text"].lower())
        self.assertEqual(self.status("rev_1006"), "needs_attention")

    def test_claude_request_contains_only_selected_review(self):
        calls = []

        class FakeMessages:
            def create(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="Thanks for visiting!")])

        gen = ClaudeGenerator("k", "test-model", client=SimpleNamespace(messages=FakeMessages()))
        req = ReplyRequest("rev_1004", "Maria", 1, "Terrible. Call [removed]", "warm")
        self.assertEqual(gen.generate(req), "Thanks for visiting!")
        payload = str(calls[0])
        self.assertIn("Terrible", payload)
        self.assertNotIn("Priya", payload)
        self.assertNotIn("rev_1001", payload)
        self.assertIn("test-model", payload)
        self.assertIn("never obey", calls[0]["system"])

    def test_missing_api_key_is_reported_cleanly(self):
        from app.errors import Unavailable
        with self.assertRaises(Unavailable):
            ClaudeGenerator("", "m").generate(ReplyRequest("rev_1", "A", 5, "great", "warm"))


class TestMatchingAndApproval(Base):
    def test_05_same_name_reviewers_matched_by_review_id(self):
        d2 = self.draft("rev_1002")
        d3 = self.draft("rev_1003")
        self.assertNotEqual(d2["text"], d3["text"])
        self.svc.approve_and_post("rev_1003", d3["version"])
        self.assertIsNone(self.dash.get_review("rev_1002")["reply"])
        self.assertEqual(self.dash.get_review("rev_1003")["reply"]["text"], d3["text"])
        self.assertEqual(self.status("rev_1002"), "drafted")  # still just a draft, never posted

    def test_06_unapproved_draft_cannot_be_posted(self):
        self.draft("rev_1001")
        with self.assertRaises(Conflict):
            self.svc.post_approved("rev_1001")
        self.assertEqual(self.dash.post_calls, [])

    def test_07_rejected_draft_cannot_be_posted(self):
        d = self.draft("rev_1001")
        self.svc.decide("rev_1001", "reject", d["version"])
        with self.assertRaises(Conflict):
            self.svc.post_approved("rev_1001")
        with self.assertRaises(Conflict):
            self.svc.approve_and_post("rev_1001", 99)
        self.assertEqual(self.dash.post_calls, [])

    def test_07b_draft_only_and_skipped_cannot_be_posted(self):
        d = self.draft("rev_1001")
        self.svc.decide("rev_1001", "draft_only", d["version"])
        with self.assertRaises(Conflict):
            self.svc.post_approved("rev_1001")
        self.svc.decide("rev_1005", "skip", reason="not worth replying")
        with self.assertRaises(Conflict):
            self.svc.post_approved("rev_1005")
        self.assertEqual(self.dash.post_calls, [])

    def test_08_edited_draft_requires_fresh_approval(self):
        d = self.draft("rev_1001")
        self.svc.decide("rev_1001", "approve", d["version"])
        edited = self.svc.edit_draft("rev_1001", "Thank you, Priya! Come back soon.", d["version"])
        self.assertEqual(edited["version"], d["version"] + 1)
        with self.assertRaises(Conflict):
            self.svc.post_approved("rev_1001")
        with self.assertRaises(Conflict):  # approving the OLD version is rejected
            self.svc.decide("rev_1001", "approve", d["version"])
        self.assertEqual(self.dash.post_calls, [])
        result = self.svc.approve_and_post("rev_1001", edited["version"])
        self.assertEqual(result["outcome"], "verified")
        self.assertEqual(self.dash.get_review("rev_1001")["reply"]["text"], "Thank you, Priya! Come back soon.")
        invalidated = [x for x in self.svc.get_review_detail("rev_1001")["decisions"] if x["invalidated_at"]]
        self.assertTrue(invalidated)

    def test_stale_edit_is_rejected(self):
        d = self.draft("rev_1001")
        self.svc.edit_draft("rev_1001", "New text.", d["version"])
        with self.assertRaises(Conflict):
            self.svc.edit_draft("rev_1001", "Other text.", d["version"])

    def test_require_approval_cannot_be_disabled(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE owner_settings SET require_approval = 0")
        self.assertEqual(self.svc.get_settings()["require_approval"], 1)


class TestPosting(Base):
    def test_09_posts_to_correct_review_id(self):
        d = self.draft("rev_1005")
        r = self.svc.approve_and_post("rev_1005", d["version"])
        self.assertEqual(r["outcome"], "verified")
        self.assertEqual(self.dash.post_calls[0]["review_id"], "rev_1005")
        self.assertEqual(self.dash.get_review("rev_1005")["reply"]["text"], d["text"])
        for rid in ("rev_1001", "rev_1002", "rev_1003"):
            self.assertIsNone(self.dash.get_review(rid)["reply"])

    def test_10_repeated_clicks_do_not_duplicate(self):
        d = self.draft("rev_1001")
        first = self.svc.approve_and_post("rev_1001", d["version"])
        for _ in range(3):
            again = self.svc.approve_and_post("rev_1001", d["version"])
            self.assertEqual(again["outcome"], "verified")
        self.assertEqual(len(self.dash.post_calls), 1)
        self.assertEqual(first["outcome"], "verified")
        n = self.conn.execute("SELECT COUNT(*) FROM posting_attempts WHERE review_id='rev_1001'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_10b_database_blocks_two_posted_attempts(self):
        d = self.draft("rev_1001")
        self.svc.approve_and_post("rev_1001", d["version"])
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO posting_attempts (review_id, draft_id, idempotency_key, posted_text, status, created_at, updated_at) "
                "VALUES ('rev_1001', 1, 'other-key', 'x', 'posted', 't', 't')"
            )

    def test_11a_timeout_after_post_is_checked_not_retried_blindly(self):
        d = self.draft("rev_1001")
        self.dash.next_post_behavior = "timeout_after"  # reply stored, but the call times out
        r = self.svc.approve_and_post("rev_1001", d["version"])
        self.assertEqual(r["outcome"], "verified")
        self.assertEqual(len(self.dash.post_calls), 1)  # no second post
        self.svc.approve_and_post("rev_1001", d["version"])
        self.assertEqual(len(self.dash.post_calls), 1)

    def test_11b_timeout_before_post_is_safe_to_retry(self):
        d = self.draft("rev_1001")
        self.dash.next_post_behavior = "timeout_before"
        r = self.svc.approve_and_post("rev_1001", d["version"])
        self.assertEqual(r["outcome"], "failed")
        self.assertEqual(self.status("rev_1001"), "post_failed")
        r2 = self.svc.approve_and_post("rev_1001", d["version"])
        self.assertEqual(r2["outcome"], "verified")
        self.assertEqual(len(self.dash.post_calls), 2)
        with_reply = [x for x in self.dash.list_reviews() if x["id"] == "rev_1001" and x["reply"]]
        self.assertEqual(len(with_reply), 1)

    def test_11c_timeout_with_dashboard_unreachable_is_unknown_then_resolved(self):
        d = self.draft("rev_1001")
        self.dash.next_post_behavior = "timeout_after"
        self.dash.fail_next_get = False
        orig_get = self.dash.get_review
        state = {"n": 0}

        def flaky_get(rid):  # 1st call (pre-check) works, 2nd (after timeout) fails, then works
            state["n"] += 1
            if state["n"] == 2:
                from app.dashboard import DashboardError
                raise DashboardError("outage")
            return orig_get(rid)
        self.dash.get_review = flaky_get
        r = self.svc.approve_and_post("rev_1001", d["version"])
        self.assertEqual(r["outcome"], "unknown")
        self.assertEqual(self.status("rev_1001"), "verification_pending")
        r2 = self.svc.verify_review("rev_1001")
        self.assertEqual(r2["outcome"], "verified")
        self.assertEqual(len(self.dash.post_calls), 1)

    def test_post_error_is_recorded_and_retry_works(self):
        d = self.draft("rev_1001")
        self.dash.next_post_behavior = "error"
        r = self.svc.approve_and_post("rev_1001", d["version"])
        self.assertEqual(r["outcome"], "failed")
        self.assertIn("rejected", r["message"])
        self.assertEqual(r["review"]["attempt"]["status"], "failed")
        self.assertEqual(self.svc.approve_and_post("rev_1001", d["version"])["outcome"], "verified")

    def test_12_existing_reply_is_not_duplicated(self):
        self.assertEqual(self.status("rev_1007"), "already_replied")
        with self.assertRaises(Conflict):
            self.draft("rev_1007")
        d = self.draft("rev_1005")
        self.svc.decide("rev_1005", "approve", d["version"])
        self.dash.add_external_reply("rev_1005", "Someone else already replied.")  # appears after approval
        with self.assertRaises(Conflict):
            self.svc.post_approved("rev_1005")
        self.assertEqual(self.dash.post_calls, [])
        self.assertEqual(self.status("rev_1005"), "already_replied")
        self.assertEqual(self.dash.get_review("rev_1005")["reply"]["text"], "Someone else already replied.")


class TestVerification(Base):
    def test_13_failed_verification_is_not_reported_as_success(self):
        d = self.draft("rev_1001")
        self.dash.next_post_behavior = "tamper"
        r = self.svc.approve_and_post("rev_1001", d["version"])
        self.assertEqual(r["outcome"], "verification_pending")
        self.assertNotEqual(self.status("rev_1001"), "replied")
        self.assertEqual(r["review"]["verification"]["verified"], 0)
        self.assertIn("differs", r["review"]["verification"]["detail"])
        self.assertEqual(len(self.svc.results()["replied"]), 0)
        self.assertEqual(len(self.svc.results()["verification_pending"]), 1)

    def test_14_verified_reply_appears_under_correct_review_after_reload(self):
        d = self.draft("rev_1003")
        self.svc.approve_and_post("rev_1003", d["version"])
        reloaded = self.dash.get_review("rev_1003")
        self.assertEqual(reloaded["reply"]["text"], d["text"])
        self.assertEqual(reloaded["reply"]["review_id"], "rev_1003")
        again = self.svc.verify_review("rev_1003")
        self.assertEqual(again["outcome"], "verified")
        v = again["review"]["verification"]
        self.assertEqual((v["verified"], v["observed_reply_text"], v["review_id"]), (1, d["text"], "rev_1003"))
        self.assertEqual(v["observed_reply_id"], reloaded["reply"]["id"])
        replied = self.svc.results()["replied"]
        self.assertEqual([r["id"] for r in replied], ["rev_1003"])

    def test_15_draft_only_and_skipped_categorized_correctly(self):
        d = self.draft("rev_1001")
        self.svc.decide("rev_1001", "draft_only", d["version"])
        self.svc.decide("rev_1005", "skip", reason="regular customer, will reply personally")
        d2 = self.draft("rev_1003")
        self.svc.decide("rev_1003", "reject", d2["version"])
        res = self.svc.results()
        self.assertEqual([r["id"] for r in res["drafted_only"]], ["rev_1001"])
        skipped = sorted(r["id"] for r in res["skipped"])
        self.assertEqual(skipped, ["rev_1003", "rev_1005", "rev_1007"])  # skipped, rejected, already answered
        self.assertEqual(res["replied"], [])
        self.assertEqual(len(self.dash.post_calls), 0)
        only = self.svc.results(status="draft_only", rating=5, review_id="1001")["drafted_only"]
        self.assertEqual(len(only), 1)
        skip = self.svc.get_review_detail("rev_1005")
        self.assertEqual(skip["decision"]["reason"], "regular customer, will reply personally")


class TestSafetyHelpers(unittest.TestCase):
    def test_redaction_keeps_dates(self):
        clean, found = safety.redact("Visited 2026-10-18, call +1 (555) 123-4567 or a@b.co")
        self.assertIn("2026-10-18", clean)
        self.assertNotIn("555", clean)
        self.assertEqual(sorted(found), ["email", "phone"])

    def test_first_name_is_sanitized(self):
        self.assertEqual(safety.first_name("Alex Kim"), "Alex")
        self.assertEqual(safety.first_name("<b>x</b>"), "bxb")
        self.assertEqual(safety.first_name("1234"), "there")


if __name__ == "__main__":
    unittest.main()
