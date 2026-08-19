from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr, field_validator, model_validator
import os

# Modos válidos de cualquier integrador con una obra social (Sancor/Nobis/OSPJN).
#   simulado   → no sale ningún request; no hace falta URL ni credencial
#   test       → contra el ambiente de pruebas de la O.S.
#   produccion → contra el ambiente real; consume tokens/credenciales reales
_MODOS_INTEGRADOR = {"simulado", "test", "produccion"}


def _valor_de(campo: object) -> str:
    """Extrae el string de un campo que puede ser `SecretStr` o `str`, para
    poder chequear si vino vacío sin loguear el secreto."""
    if isinstance(campo, SecretStr):
        return campo.get_secret_value().strip()
    return (campo or "").strip()


def _exigir_config_integrador(
    *, modo_campo: str, modo: str, requeridos_por_modo: dict[str, dict[str, object]]
) -> None:
    """Si el modo no es "simulado", exige que las variables de ESE modo (test o
    producción) no estén vacías. En "simulado" no se manda ningún request, así
    que no hace falta URL ni credencial para levantar el entorno."""
    if modo == "simulado":
        return
    faltantes = [
        nombre
        for nombre, valor in requeridos_por_modo.get(modo, {}).items()
        if not _valor_de(valor)
    ]
    if faltantes:
        raise ValueError(
            f"{modo_campo}={modo!r} necesita, sin estar vacías: {', '.join(faltantes)}."
        )


class Settings(BaseSettings):
    # Sin esto, Settings sólo ve el entorno del proceso: funciona en Docker
    # (docker-compose inyecta .env vía `env_file`) pero no corriendo uvicorn a
    # mano. extra="ignore" es OBLIGATORIO: el `.env` real trae claves que acá no
    # se declaran (MYSQL_ROOT_PASSWORD, PMA_*, SSH, Password...) y el default de
    # pydantic-settings es extra="forbid", que haría fallar el arranque por cada
    # una de ellas.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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

    # ── Facturación — carga sin precio ───────────────────────────────────────
    # False por defecto: un código sin precio vigente para esa OS/fecha/perfil del
    # médico rechaza la carga con 422 (ver `resolver_precio` en
    # modules/facturacion/service.py). True permite cargar la prestación igual,
    # con honorarios/gastos/ayudante en 0 — pensado para probar el flujo de carga
    # sin depender de que el nomenclador esté completo. NO evita otros rechazos
    # (habilitación del médico, fecha inválida, vía no aplicable): esos siguen
    # bloqueando igual.
    #
    # ⚠️ Es un permiso de FACTURACIÓN y nada más. **El módulo de validaciones lo
    # ignora**: ahí un código que cotiza $ 0 se rechaza siempre, con este flag en
    # true o en false (`validaciones/core/contrato.py::factura_en_cero`). La
    # diferencia es quién carga y qué puede hacer después: en facturación el
    # operador del Colegio ve el 0 y lo corrige antes de cerrar el período; en el
    # panel del prestador la fila se graba sola, entra a la liquidación y el
    # médico se entera cuando cobra de menos — y si la obra social valida en
    # línea, la autorización ya consumió el token del afiliado.
    CARGA_SIN_PRECIO: bool = False

    @property
    def IS_PRODUCTION(self) -> bool:
        return self.ENV.strip().lower() in {"production", "prod"}

    # ── Sancor Salud (O.S. 411) — autorizador HL7 v2.4 sobre SOAP ─────────────
    # MODO controla a dónde se manda cada autorización. SIN DEFAULT a propósito:
    # si falta en el .env, la API no arranca en vez de asumir un modo — así
    # nadie dispara autorizaciones reales contra Sancor por levantar el entorno
    # sin darse cuenta de qué valor quedó puesto.
    #   simulado   → no sale ningún request; se devuelve una respuesta armada
    #   test       → testservicios.sancorsalud.com.ar (MSH processing-ID = D)
    #   produccion → servicios.sancorsalud.com.ar     (MSH processing-ID = P)
    SANCOR_MODO: str
    SANCOR_URL_TEST: str = ""
    SANCOR_URL_PROD: str = ""
    # CUIT del Colegio y código de prestador ante Sancor. Van en el mensaje HL7
    # SIEMPRE, incluso en modo simulado (se arma igual para mostrar qué se
    # mandaría) — por eso no dependen del modo activo, a diferencia de las URLs
    # y el pasaporte. `SANCOR_PRESTADOR` va en `PRD|PS^Prestador Solicitante` de
    # la anulación (Z04) — confirmado contra ~14.000 respuestas reales en
    # `mensajeria_sancor.txt`, donde Sancor siempre devuelve
    # `PRD|PS^Prestador Solicitante|COLEGIO MEDICO DE CORRIENTES|||||46632^PR`.
    # No es la matrícula de ningún médico particular.
    SANCOR_CUIT: str
    SANCOR_PRESTADOR: str
    SANCOR_PASAPORTE_TEST: SecretStr = SecretStr("")
    SANCOR_PASAPORTE_PROD: SecretStr = SecretStr("")
    SANCOR_TIMEOUT: int = 30

    # ── Nobis Salud (O.S. 62) — WSGeCROS ─────────────────────────────────────
    # Mismo criterio que Sancor: sin default. Insertar una orden (y anularla) es
    # un efecto real en el sistema de Nobis.
    #   simulado   → no sale ningún request; se devuelve una respuesta armada
    #   test       → wstest.nobissalud.com:7004
    #   produccion → servicioweb.nobissalud.com.ar
    NOBIS_MODO: str
    NOBIS_URL_TEST: str = ""
    NOBIS_URL_PROD: str = ""
    # Credenciales del Colegio ante el WS — van en `.env`, nunca en el código.
    NOBIS_USUARIO: str = ""
    NOBIS_CLAVE: SecretStr = SecretStr("")
    # Fijo por convenio: "90692 - Colegio Medico de Corrientes" es la entidad
    # efectora de todas las órdenes. No es secreto, se queda con default.
    NOBIS_COD_ENTIDAD_EFECTORA: str = "90692"
    # Tipo de solicitante que espera Gecros para los profesionales del Colegio.
    NOBIS_TIPO_SOLIC: str = "12221"
    NOBIS_TIMEOUT: int = 30

    # ── OSPJN · Poder Judicial (O.S. 151) — REST ─────────────────────────────
    # Mismo criterio que Sancor y Nobis: sin default.
    #   simulado   → no sale ningún request; se devuelve una respuesta armada
    #   test       → api-test.ospjn.gov.ar
    #   produccion → api.ospjn.gov.ar  (⚠️ SIN CONFIRMAR — ver InfoValidaciones/ospjn.md)
    OSPJN_MODO: str
    OSPJN_URL_TEST: str = ""
    OSPJN_URL_PROD: str = ""
    # Usuario de API del Colegio (no del prestador) — van en `.env`.
    OSPJN_USUARIO: str = ""
    OSPJN_PASSWORD: SecretStr = SecretStr("")
    OSPJN_TIMEOUT: int = 30

    @field_validator("SANCOR_MODO", "NOBIS_MODO", "OSPJN_MODO", mode="after")
    @classmethod
    def _validar_modo_integrador(cls, v: str, info) -> str:
        v = (v or "").strip().lower()
        if v not in _MODOS_INTEGRADOR:
            raise ValueError(
                f"{info.field_name} tiene que ser uno de {sorted(_MODOS_INTEGRADOR)} "
                f"(vino: {v!r})."
            )
        return v

    @model_validator(mode="after")
    def _validar_integradores_configurados(self) -> "Settings":
        _exigir_config_integrador(
            modo_campo="SANCOR_MODO",
            modo=self.SANCOR_MODO,
            requeridos_por_modo={
                "test": {
                    "SANCOR_URL_TEST": self.SANCOR_URL_TEST,
                    "SANCOR_PASAPORTE_TEST": self.SANCOR_PASAPORTE_TEST,
                },
                "produccion": {
                    "SANCOR_URL_PROD": self.SANCOR_URL_PROD,
                    "SANCOR_PASAPORTE_PROD": self.SANCOR_PASAPORTE_PROD,
                },
            },
        )
        _exigir_config_integrador(
            modo_campo="NOBIS_MODO",
            modo=self.NOBIS_MODO,
            requeridos_por_modo={
                "test": {
                    "NOBIS_URL_TEST": self.NOBIS_URL_TEST,
                    "NOBIS_USUARIO": self.NOBIS_USUARIO,
                    "NOBIS_CLAVE": self.NOBIS_CLAVE,
                },
                "produccion": {
                    "NOBIS_URL_PROD": self.NOBIS_URL_PROD,
                    "NOBIS_USUARIO": self.NOBIS_USUARIO,
                    "NOBIS_CLAVE": self.NOBIS_CLAVE,
                },
            },
        )
        _exigir_config_integrador(
            modo_campo="OSPJN_MODO",
            modo=self.OSPJN_MODO,
            requeridos_por_modo={
                "test": {
                    "OSPJN_URL_TEST": self.OSPJN_URL_TEST,
                    "OSPJN_USUARIO": self.OSPJN_USUARIO,
                    "OSPJN_PASSWORD": self.OSPJN_PASSWORD,
                },
                "produccion": {
                    "OSPJN_URL_PROD": self.OSPJN_URL_PROD,
                    "OSPJN_USUARIO": self.OSPJN_USUARIO,
                    "OSPJN_PASSWORD": self.OSPJN_PASSWORD,
                },
            },
        )
        return self

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

