"""FastAPI app: thin HTTP layer over ReviewService. All business rules live in services.py."""
from typing import Literal, Optional

from fastapi import FastAPI, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pathlib import Path
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .dashboard import MockDashboard, OrganizerDashboard
from .db import connect
from .errors import ServiceError
from .generator import GeminiGenerator, MockGenerator
from .sample_data import SAMPLE_REVIEWS
from .services import ReviewService

REVIEW_ID = Path(..., pattern=r"^[A-Za-z0-9_\-]{1,64}$")


class SettingsIn(BaseModel):
    tone: str = Field(min_length=1, max_length=40)
    extra_instructions: str = Field(default="", max_length=500)


class EditIn(BaseModel):
    text: str = Field(min_length=1, max_length=1500)
    base_version: int = Field(ge=0)


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject", "skip", "draft_only"]
    version: Optional[int] = Field(default=None, ge=1)
    reason: Optional[str] = Field(default=None, max_length=300)


class VersionIn(BaseModel):
    version: int = Field(ge=1)


def build_dashboard(settings: Settings):
    if settings.dashboard_mode == "organizer":
        return OrganizerDashboard()
    dash = MockDashboard(settings.dashboard_db_path)
    if dash.is_empty():
        dash.seed(SAMPLE_REVIEWS)
    return dash


def build_generator(settings: Settings):
    if settings.ai_provider == "mock":
        return MockGenerator()
    if settings.ai_provider == "gemini":
        return GeminiGenerator(settings.gemini_api_key, settings.gemini_model)


def create_app(settings: Settings = None, service: ReviewService = None) -> FastAPI:
    settings = settings or Settings()
    if service is None:
        service = ReviewService(connect(settings.db_path), build_dashboard(settings), build_generator(settings))
        try:
            service.sync_reviews()
        except ServiceError:
            pass  # the UI has a Sync button and shows the error
    app = FastAPI(title="AI Restaurant Review Reply Agent")
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"]
    )

    @app.exception_handler(ServiceError)
    def service_error_handler(_request, exc: ServiceError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.get("/api/health")
    def health():
        return {"ok": True, "ai_provider": settings.ai_provider, "dashboard_mode": settings.dashboard_mode}

    @app.get("/api/summary")
    def summary():
        return service.summary()

    @app.get("/api/settings")
    def get_settings():
        return service.get_settings()

    @app.put("/api/settings")
    def put_settings(body: SettingsIn):
        return service.update_settings(body.tone, body.extra_instructions)

    @app.post("/api/reviews/sync")
    def sync():
        return service.sync_reviews()

    @app.get("/api/reviews")
    def list_reviews(
        status: Optional[str] = Query(default=None, max_length=40),
        rating: Optional[int] = Query(default=None, ge=1, le=5),
        review_id: Optional[str] = Query(default=None, max_length=64),
    ):
        return service.list_reviews(status, rating, review_id)

    @app.get("/api/reviews/{review_id}")
    def get_review(review_id: str = REVIEW_ID):
        return service.get_review_detail(review_id)

    @app.post("/api/reviews/{review_id}/draft")
    def generate(review_id: str = REVIEW_ID):
        return service.generate_draft(review_id)

    @app.put("/api/reviews/{review_id}/draft")
    def edit(body: EditIn, review_id: str = REVIEW_ID):
        return service.edit_draft(review_id, body.text, body.base_version)

    @app.post("/api/reviews/{review_id}/decision")
    def decide(body: DecisionIn, review_id: str = REVIEW_ID):
        return service.decide(review_id, body.decision, body.version, body.reason)

    @app.post("/api/reviews/{review_id}/approve-and-post")
    def approve_and_post(body: VersionIn, review_id: str = REVIEW_ID):
        return service.approve_and_post(review_id, body.version)

    @app.post("/api/reviews/{review_id}/verify")
    def verify(review_id: str = REVIEW_ID):
        return service.verify_review(review_id)

    @app.get("/api/results")
    def results(
        status: Optional[str] = Query(default=None, max_length=40),
        rating: Optional[int] = Query(default=None, ge=1, le=5),
        review_id: Optional[str] = Query(default=None, max_length=64),
    ):
        return service.results(status, rating, review_id)
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")

    return app


app = create_app()
