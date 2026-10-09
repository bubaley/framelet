from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='FRAMELET_', extra='ignore')

    api_token: SecretStr = Field(min_length=16)
    max_video_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)
    max_frame_pixels: int = Field(default=4096 * 4096, ge=1, le=8192 * 8192)
    max_frame_bytes: int = Field(default=64 * 1024 * 1024, ge=1024, le=256 * 1024 * 1024)
    render_timeout_seconds: float = Field(default=15, ge=0.1, le=120)
    startup_timeout_seconds: float = Field(default=30, ge=1, le=120)
    temp_directory: str | None = None

    @property
    def max_request_bytes(self) -> int:
        return self.max_video_bytes + 64 * 1024
