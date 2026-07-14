"""Environment-driven settings (plan section 2). Prefix: HS_ (e.g. HS_JWT_SECRET)."""
from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/app.db"
    jwt_secret: str = "dev-secret-change-me"
    access_ttl_min: int = 60
    refresh_ttl_days: int = 7
    cors_origins: str = "http://localhost:5173"
    login_rate_per_minute: int = 10
    bcrypt_rounds: int = 12
    # Seed the real 14-nurse roster on an EMPTY database at startup (plan 5).
    # Tests disable it so they keep their empty-DB assumptions.
    seed_default_staff: bool = True

    model_config = {"env_prefix": "HS_", "env_file": ".env", "extra": "ignore"}
