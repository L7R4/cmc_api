from pydantic_settings import BaseSettings
from pydantic import SecretStr
import os

class Settings(BaseSettings):
    MYSQL_USER: str
    MYSQL_PASS: str
    MYSQL_HOST: str
    MYSQL_DB: str
    MYSQL_PORT: int | None = 3306

    CORS_ORIGINS: str
    JWT_SECRET: SecretStr                 
    JWT_ALG: str = "HS256"
    ACCESS_MINUTES: int = 15
    REFRESH_DAYS: int = 15
    
    COOKIE_SAMESITE: str
    COOKIE_SECURE: bool = False  
    COOKIE_DOMAIN: str | None = None

    FRONT_BASE_URL: str | None = None
    ALLOWED_FRONT_HOSTS: str | None = None

    LEGACY_BASE_URL: str | None = None 
    LEGACY_SSO_PATH: str = "/sso_login.php"
    LEGACY_SSO_SECRET: SecretStr | None = None
    
    MEDIA_ROOT: str = "uploads"   
    MEDIA_URL: str = "uploads"        
    MEDIA_BASE_URL: str | None = None   
                 
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

    def ALLOWED_FRONT_HOSTS_LIST(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_FRONT_HOSTS.split(',') if o.strip()]

# Carga valores desde .env o .env.prod según entorno que se defina en docker-compose
settings = Settings()

