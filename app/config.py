import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_ENV: str = "development"
    API_V1_PREFIX: str = "/api/v1"
    SECRET_KEY: str = "dev_secret_key_change_in_production_jwt_signature_32char"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # Database
    DATABASE_URL: str = "sqlite:///./cognifolio.db"

    # Vector Database (Qdrant)
    QDRANT_LOCATION: str = "./qdrant_storage"
    QDRANT_API_KEY: str = ""
    QDRANT_COLLECTION: str = "cognifolio_embeddings"

    # AI Models
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash"
    EMBEDDING_MODEL: str = "gemini-embedding-001"

    # Storage
    UPLOAD_DIR: str = "./uploads"
    MAX_FILE_SIZE_MB: int = 25
    ALLOWED_EXTENSIONS: str = "pdf,png,jpg,jpeg,webp,docx,doc,xlsx,xls,csv,txt,md"

    @property
    def allowed_extensions_list(self) -> list[str]:
        return [ext.strip().lower() for ext in self.ALLOWED_EXTENSIONS.split(",")]


settings = Settings()

# Ensure directories exist
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
if settings.QDRANT_LOCATION.startswith("./"):
    os.makedirs(settings.QDRANT_LOCATION, exist_ok=True)
