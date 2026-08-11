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

    # ── Entorno ───────────────────────────────────────────────────────────────
    # "production" apaga /docs, /redoc y /openapi.json. Cualquier otro valor los
    # deja activos para desarrollo. Se setea en docker-compose.prod.yml.
    ENV: str = "development"

    # ── Uploads ───────────────────────────────────────────────────────────────
    # Tamaño máximo por archivo subido, en bytes. 20 MB.
    MAX_UPLOAD_BYTES: int = 20 * 1024 * 1024

    # ── Tamaño de request que NO es un archivo ────────────────────────────────
    # 2 MB. El punto de partida es el body legítimo más grande que acepta la
    # API: `POST /api/valores_nm/actualizar_por_codigos`, un item por código del
    # nomenclador. Con 5.175 códigos y ~75 bytes por item son ~390 KB, así que
    # 2 MB deja ~5x de margen — cubre que el nomenclador crezca al doble y que
    # alguien mande el JSON indentado, sin dejar de ser tres órdenes de magnitud
    # menos que "sin límite", que es lo que había.
    #
    # NO aplica a `multipart/form-data`: esos van por MAX_UPLOAD_BYTES, que los
    # valida por archivo y además chequea el tipo por magic bytes.
    # Ver app/middleware/body_limit.py y S2 en docs/api/AUDITORIA_SEGURIDAD.md.
    MAX_JSON_BODY_BYTES: int = 2 * 1024 * 1024

    # ── RBAC ──────────────────────────────────────────────────────────────────
    # False = modo observación: si a un usuario le falta el scope se registra un
    # WARNING en el log y la request pasa igual. True = rechazo real con 403.
    #
    # Arranca en False A PROPÓSITO. Cerrar 301 endpoints de golpe rompe pantallas
    # del front sin aviso; una o dos semanas de tráfico real en observación dicen
    # exactamente qué rol necesita qué. Ver docs/api/RBAC_PROPUESTA.md §7, Etapa 1.
    RBAC_ENFORCE: bool = False

    # ── Rate limiting de autenticación ────────────────────────────────────────
    # Cuenta solo INTENTOS FALLIDOS, en ventana deslizante. Ver app/core/ratelimit.py.
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_WINDOW_SECONDS: int = 15 * 60
    # Por IP: tolerante, porque un consultorio o el NAT de un hospital comparten
    # salida entre varios médicos.
    RATE_LIMIT_MAX_PER_IP: int = 20
    # Por cuenta: estricto. Es el que corta el password spraying, y un usuario
    # legítimo no falla 8 veces seguidas en 15 minutos.
    RATE_LIMIT_MAX_PER_SOCIO: int = 8

    @property
    def IS_PRODUCTION(self) -> bool:
        return self.ENV.strip().lower() in {"production", "prod"}

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

    # ── Nobis Salud (O.S. 402) — WSGeCROS ────────────────────────────────────
    # Mismo criterio que Sancor: arranca en "simulado" y no sale ningún request
    # hasta cambiarlo a propósito. Insertar una orden (y anularla) es un efecto
    # real en el sistema de Nobis.
    #   simulado   → no sale ningún request; se devuelve una respuesta armada
    #   test       → wstest.nobissalud.com:7004
    #   produccion → servicioweb.nobissalud.com.ar
    NOBIS_MODO: str = "simulado"
    NOBIS_URL_TEST: str = "https://wstest.nobissalud.com:7004/WSGecrosNet.asmx"
    NOBIS_URL_PROD: str = "https://servicioweb.nobissalud.com.ar/WSGecrosNet.asmx"
    # Credenciales del Colegio ante el WS. Las de producción están en el .env;
    # estos defaults son los del ambiente de prueba documentado en el legacy.
    NOBIS_USUARIO: str = "CMCORR"
    NOBIS_CLAVE: str = "nobis2025"
    # Fijo por convenio: "90692 - Colegio Medico de Corrientes" es la entidad
    # efectora de todas las órdenes.
    NOBIS_COD_ENTIDAD_EFECTORA: str = "90692"
    # Tipo de solicitante que espera Gecros para los profesionales del Colegio.
    NOBIS_TIPO_SOLIC: str = "12221"
    NOBIS_TIMEOUT: int = 30

    # ── OSPJN · Poder Judicial (O.S. 151) — REST ─────────────────────────────
    # Mismo criterio que Sancor y Nobis: arranca en "simulado".
    #   simulado   → no sale ningún request; se devuelve una respuesta armada
    #   test       → api-test.ospjn.gov.ar
    #   produccion → api.ospjn.gov.ar  (⚠️ SIN CONFIRMAR — ver InfoValidaciones/ospjn.md)
    OSPJN_MODO: str = "simulado"
    OSPJN_URL_TEST: str = "https://api-test.ospjn.gov.ar/ospjn.prestadores.api/PrestadorService.svc/rest"
    OSPJN_URL_PROD: str = "https://api.ospjn.gov.ar/ospjn.prestadores.api/PrestadorService.svc/rest"
    # Usuario de API del Colegio (no del prestador). El token sale de /Ingresar
    # con estas credenciales; las de producción van en el .env.
    OSPJN_USUARIO: str = "api-cmc-test"
    OSPJN_PASSWORD: str = "CMed.usertest!"
    OSPJN_TIMEOUT: int = 30
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

