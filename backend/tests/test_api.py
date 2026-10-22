"""HTTP-level smoke tests. Skipped automatically if FastAPI/httpx are not installed."""
import unittest

try:
    from fastapi.testclient import TestClient
    HAVE_FASTAPI = True
except Exception:  # pragma: no cover
    HAVE_FASTAPI = False

if HAVE_FASTAPI:
    from app.config import Settings
    from app.dashboard import MockDashboard
    from app.db import connect
    from app.generator import MockGenerator
    from app.main import create_app
    from app.sample_data import SAMPLE_REVIEWS
    from app.services import ReviewService


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx not installed")
class TestApi(unittest.TestCase):
    def setUp(self):
        dash = MockDashboard(":memory:")
        dash.seed(SAMPLE_REVIEWS)
        self.dash = dash
        svc = ReviewService(connect(":memory:"), dash, MockGenerator())
        svc.sync_reviews()
        self.client = TestClient(create_app(Settings(), svc))

    def test_full_flow(self):
        c = self.client
        self.assertEqual(len(c.get("/api/reviews").json()), 8)
        d = c.post("/api/reviews/rev_1001/draft").json()
        self.assertEqual(d["review_id"], "rev_1001")
        r = c.post("/api/reviews/rev_1001/approve-and-post", json={"version": d["version"]}).json()
        self.assertEqual(r["outcome"], "verified")
        self.assertEqual(len(c.get("/api/results").json()["replied"]), 1)

    def test_unapproved_post_is_refused_and_bad_input_validated(self):
        c = self.client
        c.post("/api/reviews/rev_1001/draft")
        self.assertEqual(c.post("/api/reviews/rev_1001/verify").status_code, 409)
        self.assertEqual(c.get("/api/reviews/bad id!").status_code, 422)
        self.assertEqual(c.put("/api/settings", json={"tone": ""}).status_code, 422)
        self.assertEqual(c.get("/api/reviews/rev_9999").status_code, 404)
        self.assertEqual(self.dash.post_calls, [])
