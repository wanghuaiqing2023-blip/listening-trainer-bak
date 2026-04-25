from pydantic_settings import BaseSettings
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Azure Speech
    azure_speech_key: str = ""
    azure_speech_region: str = "eastasia"

    # Anthropic Claude
    anthropic_api_key: str = ""
    anthropic_auth_token: str = ""        # OpenRouter / proxy Bearer token
    anthropic_base_url: str = ""          # OpenRouter: https://openrouter.ai/api
    anthropic_model: str = "claude-sonnet-4-6"

    # Azure TTS voice
    azure_tts_voice: str = "en-US-JennyNeural"

    # Paths
    uploads_dir: Path = BASE_DIR / "data" / "uploads"
    segments_dir: Path = BASE_DIR / "data" / "audio_segments"
    db_path: str = str(BASE_DIR / "data" / "db" / "listening.db")
    ecdict_path: str = str(BASE_DIR / "backend" / "ecdict.db")
    model_assets_dir: Path = BASE_DIR / "data" / "models"
    whisper_assets_dir: Path = BASE_DIR / "data" / "models" / "whisper"
    align_assets_dir: Path = BASE_DIR / "data" / "models" / "alignment"
    artifacts_dir: Path = BASE_DIR / "data" / "artifacts"

    # Whisper
    whisper_model: str = "base"  # valid values: tiny/base/small/medium/large

    # Difficulty thresholds
    vocab_unknown_threshold: float = 0.30
    vocab_mastered_threshold: float = 0.85

    # i+1 window
    level_window: float = 1.0

    class Config:
        env_file = str(BASE_DIR / ".env")
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()

# Ensure directories exist
settings.uploads_dir.mkdir(parents=True, exist_ok=True)
settings.segments_dir.mkdir(parents=True, exist_ok=True)
Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
settings.model_assets_dir.mkdir(parents=True, exist_ok=True)
settings.whisper_assets_dir.mkdir(parents=True, exist_ok=True)
settings.align_assets_dir.mkdir(parents=True, exist_ok=True)
settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
