from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")
    artifact_root: Path = Path("../artifacts")
    max_upload_mb: int = Field(default=20, ge=1, le=100)
    max_pdf_pages: int = Field(default=5, ge=1, le=20)
    default_review_threshold: float = Field(default=0.75, ge=0, le=1)
    ocr_language: str = "eng"
    vision_provider: str = "disabled"


settings = Settings()
