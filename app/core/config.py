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

    # Secreto de máquina para endpoints llamados por el cron (no un JWT de usuario).
    CRON_SECRET: str | None = None

    # ── Sancor Salud (O.S. 411) — autorizador HL7 v2.4 sobre SOAP ─────────────
    # MODO controla a dónde se manda cada autorización. Arranca en "simulado"
    # A PROPÓSITO: así nadie dispara autorizaciones reales contra Sancor por
    # levantar el entorno. Cambiarlo es una decisión explícita.
    #   simulado   → no sale ningún request; se devuelve una respuesta armada
    #   test       → testservicios.sancorsalud.com.ar (MSH processing-ID = D)
    #   produccion → servicios.sancorsalud.com.ar     (MSH processing-ID = P)
    SANCOR_MODO: str = "simulado"
    SANCOR_URL_TEST: str = "https://testservicios.sancorsalud.com.ar/Autorizador/WSDL/HL7v24"
    SANCOR_URL_PROD: str = "https://servicios.sancorsalud.com.ar/Autorizador/HL7v24"
    # CUIT del Colegio Médico, va en los segmentos PRD del mensaje.
    SANCOR_CUIT: str = "30573190692"
    SANCOR_PASAPORTE_TEST: str = "8"
    SANCOR_PASAPORTE_PROD: str = "0"
    SANCOR_TIMEOUT: int = 30
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

