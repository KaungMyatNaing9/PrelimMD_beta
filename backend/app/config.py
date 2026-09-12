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
    vision_provider: str = "disabled"
    ocr_engine: str = "tesseract"
    vision_model: str = ""
    vision_api_key: str = ""
    vision_timeout_seconds: int = Field(default=120, ge=5, le=600)


settings = Settings()
