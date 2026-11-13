from enum import StrEnum
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class VisionMode(StrEnum):
    OPENAI = "openai"
    FAKE = "fake"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PH_", extra="ignore")

    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_model: str = "gpt-5.4-mini"
    openai_reasoning_effort: str = "low"
    vision_mode: VisionMode = VisionMode.OPENAI
    host_token: SecretStr
    public_url: str = "http://localhost:8000"

    confidence_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    vision_concurrency: int = Field(default=40, ge=1, le=200)
    vision_timeout_s: float = Field(default=15.0, gt=0)
    fake_delay_s: float = Field(default=1.0, ge=0)
    max_photo_bytes: int = Field(default=3_000_000, gt=0)

    songs_file: Path = Path("data") / "songs.json"
    music_dir: Path = Path("data") / "music"

    @model_validator(mode="after")
    def _check_consistency(self) -> "Settings":
        # Ошибка конфигурации видна при запуске, а не на первом фото посреди игры.
        if self.vision_mode == VisionMode.OPENAI and self.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is required when PH_VISION_MODE=openai")
        if len(self.host_token.get_secret_value()) < 8:
            raise ValueError("PH_HOST_TOKEN must be at least 8 characters")
        return self
