from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The project root, found from this file rather than from wherever the process happens to
# have been started.
ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Anchored to the repository, not to the working directory. As a relative path this
    # resolved against the cwd, so the same code read the configuration when started from
    # `backend/` and read nothing when started from the project root -- and read nothing
    # *silently*: `vision_api_key` came back empty and the hosted reader fell back to the
    # local model with the same message it uses when a quota is spent. A setting that
    # depends on where you were standing when you ran it is worse than one that is missing.
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    artifact_root: Path = Path("../artifacts")
    max_upload_mb: int = Field(default=20, ge=1, le=100)
    max_pdf_pages: int = Field(default=5, ge=1, le=20)
    default_review_threshold: float = Field(default=0.75, ge=0, le=1)
    ocr_language: str = "eng"
    # Where the audit trail is kept. Empty means SQLite beside the artifacts, which needs
    # nothing installed and is what a laptop gets; a PostgreSQL URL moves the same rows to
    # a server without changing any of them.
    database_url: str = ""
    # "disabled" | "ollama" (local, free, no data egress) | "anthropic" (paid API)
    # Local by default (user, 2026-09-13). The hybrid is worth +2.9 / +15.9 / +9.0 points
    # on TEST2, TEST4 and TEST5, and raises precision as well as recall (45.6% -> 47.9%),
    # so shipping it off meant shipping the fallback. `ollama` rather than a hosted
    # provider because no page leaves the machine and it needs no API key; a vision failure
    # is caught in `_analyze_image` and the geometric reading stands, so an install without
    # Ollama degrades to the previous behaviour rather than breaking.
    vision_provider: str = "ollama"
    ocr_engine: str = "tesseract"
    vision_model: str = ""
    vision_api_key: str = ""
    vision_timeout_seconds: int = Field(default=120, ge=5, le=600)


settings = Settings()
