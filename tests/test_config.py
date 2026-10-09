import pytest
from pydantic import SecretStr, ValidationError

from framelet.config import Settings


def test_token_required_and_environment_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('FRAMELET_API_TOKEN', raising=False)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]  # Exercise missing environment configuration.
    monkeypatch.setenv('FRAMELET_API_TOKEN', 'valid-environment-token')
    monkeypatch.setenv('FRAMELET_MAX_VIDEO_BYTES', '123456')
    settings = Settings()  # type: ignore[call-arg]  # Token is loaded from the environment.
    assert settings.max_video_bytes == 123456
    assert 'valid-environment-token' not in repr(settings)


@pytest.mark.parametrize('token', ['', 'short'])
def test_short_token_rejected(token: str) -> None:
    with pytest.raises(ValidationError):
        Settings(api_token=SecretStr(token))
