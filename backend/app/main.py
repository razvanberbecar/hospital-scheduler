"""FastAPI entry point (plan section 2). App factory so tests can inject settings."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.holidays import router as holidays_router
from .api.schedules import router as schedules_router
from .api.users import router as users_router
from .auth.router import router as auth_router
from .config import Settings
from .db import Base, make_engine, make_sessionmaker, run_schema_fixups
from .seed import seed_default_staff


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    run_schema_fixups(engine)

    sessionmaker_ = make_sessionmaker(engine)
    if settings.seed_default_staff:  # plan 5: real roster on an empty DB only
        with sessionmaker_() as session:
            seed_default_staff(session, settings)

    app = FastAPI(title="Hospital Scheduling API", version="0.1.0")
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker_
    app.state.login_attempts = {}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(holidays_router)
    app.include_router(schedules_router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
