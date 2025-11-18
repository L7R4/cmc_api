from pydantic_settings import BaseSettings
from pydantic import SecretStr

class Settings(BaseSettings):
    MYSQL_USER: str
    MYSQL_PASS: str
    MYSQL_HOST: str
    MYSQL_DB: str
    CORS_ORIGINS: str

    JWT_SECRET: SecretStr                  # ← SIN valor por defecto
    JWT_ALG: str = "HS256"
    ACCESS_MINUTES: int = 15
    REFRESH_DAYS: int = 15
    COOKIE_SECURE: bool = False  # True en prod (https)

    LEGACY_BASE_URL: str | None = None  # ej: "https://colegiomedicocorrientes.com"
    LEGACY_SSO_PATH: str = "/sso_login.php"
    LEGACY_SSO_SECRET: SecretStr | None = None
    
    MEDIA_ROOT: str = "uploads"    # carpeta física
    MEDIA_URL: str = "uploads"         # URL base que servís
    MEDIA_BASE_URL: str | None = None   # si querés forzar dominio (https://api.x.com)
                 
    RESEND_API_KEY: str | None = None     
    EMAIL_NOTIFY_TO: str | None = None
    EMAIL_FROM: str | None = None
    @property
    def MYSQL_URL(self) -> str:
        return (
            f"mysql+aiomysql://{self.MYSQL_USER}:"
            f"{self.MYSQL_PASS}@{self.MYSQL_HOST}/{self.MYSQL_DB}"
        )
# 
    def CORS_LIST(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(',') if o.strip()]

# Carga valores desde .env
settings = Settings(_env_file=".env")
