"""Command-line demonstration of the full workflow (mock dashboard + mock AI, no network, no API key).
Run from the backend folder:  python demo.py"""
from app.dashboard import MockDashboard
from app.db import connect
from app.generator import MockGenerator
from app.sample_data import SAMPLE_REVIEWS
from app.services import ReviewService

dash = MockDashboard(":memory:")
dash.seed(SAMPLE_REVIEWS)
svc = ReviewService(connect(":memory:"), dash, MockGenerator())
print("1. Sync:", svc.sync_reviews())
svc.update_settings("friendly")
print("2. Tone set to:", svc.get_settings()["tone"])

d = svc.generate_draft("rev_1003")
print(f"3. DRAFTED  rev_1003 v{d['version']}: {d['text']}")
print("   status:", svc.get_review_detail("rev_1003")["status"], "| dashboard reply:", dash.get_review("rev_1003")["reply"])

d2 = svc.edit_draft("rev_1003", d["text"] + " We hope to see you again soon.", d["version"])
print(f"4. EDITED   -> v{d2['version']} (any earlier approval is invalidated)")

res = svc.approve_and_post("rev_1003", d2["version"])
print("5. APPROVED + POSTED:", res["outcome"], "-", res["message"])
print("   reply ID:", res["review"]["attempt"]["dashboard_reply_id"])

reloaded = dash.get_review("rev_1003")
print("6. RELOADED from dashboard:", reloaded["reply"]["review_id"], "|", reloaded["reply"]["text"])
v = svc.verify_review("rev_1003")["review"]["verification"]
print("7. VERIFIED:", bool(v["verified"]), "|", v["detail"])

print("8. Repeat click:", svc.approve_and_post("rev_1003", d2["version"])["outcome"], "| dashboard post calls so far:", len(dash.post_calls))
print("   Same-name reviewer rev_1002 untouched:", dash.get_review("rev_1002")["reply"])

d4 = svc.generate_draft("rev_1004")
print("9. PRIVATE INFO rev_1004 (flagged:", bool(d4["flagged"]), "):", d4["text"])
svc.decide("rev_1001", "skip", reason="will reply personally")
d1 = svc.generate_draft("rev_1005"); svc.decide("rev_1005", "draft_only", d1["version"])
r = svc.results()
print("10. RESULTS:", {k: [x["id"] for x in v] for k, v in r.items()})
