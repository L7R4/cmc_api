"""Hashing y verificación de contraseñas.

## Contraseña inicial (A2 / A3 de la auditoría)

Toda cuenta nueva nace con **`PASSWORD_INICIAL`** y con
`must_change_password = True`. No con el DNI, que era el esquema anterior: el
DNI figura en cualquier trámite, lo conocen la obra social, el sanatorio y el
propio Colegio, y no caduca — o sea que era un secreto compartido y permanente.
Una contraseña única y conocida es igual de adivinable, pero **solo sirve hasta
el primer login**, porque el flag obliga a cambiarla.

Quien impone el flag no es este módulo: lo devuelven el login y `/auth/me` en
`must_change_password`, y el front redirige a la pantalla de cambio. El día que
se quiera cerrar del todo, el paso siguiente es rechazar el resto de los
endpoints mientras el flag esté activo.

## El fallback de matrícula ya no existe (A2)

`verify_and_upgrade()` aceptaba `MATRICULA_PROV` como contraseña cuando el hash
guardado no coincidía, y la promovía a hash definitivo. No expiraba: una cuenta
creada hacía dos años que nunca se usó seguía siendo accesible con su matrícula,
que es información de registro público de 4-5 dígitos. Era la mitad de la cadena
de toma de control de cuentas de §2.4. Se eliminó: hoy la única forma de entrar
es con el hash guardado.

## Contraseña provisoria ("Renovar contraseña")

Cuando un socio se olvida la contraseña, el personal del Colegio la renueva
desde el legajo (`POST /api/medicos/{id}/renovar-password`). No vuelve a
`PASSWORD_INICIAL`: se genera una **aleatoria**, que el operativo ve una sola
vez para dictársela, y que **vence** a las `HORAS_PASSWORD_PROVISORIA`. El
vencimiento vive en `listado_medico.password_provisoria_vence`.

La del alta sigue siendo `PASSWORD_INICIAL` y no vence: así lo decidió el
Colegio. Lo que comparten las dos es `must_change_password`.
"""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from passlib.context import CryptContext
from app.db.models import ListadoMedico

_pwd = CryptContext(
    schemes=["pbkdf2_sha256", "bcrypt", "bcrypt_sha256"],
    deprecated=["bcrypt", "bcrypt_sha256"],
    pbkdf2_sha256__rounds=480_000,
)

MIN_PASSWORD_LENGTH = 6

# Contraseña con la que nace toda cuenta, tanto por el alta pública como por la
# administrativa. Es pública a propósito —el médico la recibe por teléfono o en
# el mostrador— y por eso viaja siempre junto a `must_change_password = True`.
PASSWORD_INICIAL = "cmc1785"

# Cuánto dura la contraseña provisoria de "Renovar contraseña". Alcanza para que
# el socio la reciba por teléfono o WhatsApp y entre; si no entró, se pide otra.
HORAS_PASSWORD_PROVISORIA = 48

# Sin caracteres que se confunden al dictarla o al leerla en pantalla
# (0/O, 1/l/I) y sin mayúsculas: se dicta por teléfono.
_ALFABETO_PROVISORIA = "abcdefghjkmnpqrstuvwxyz23456789"

MENSAJE_PROVISORIA_VENCIDA = (
    "Tu contraseña provisoria venció. Pedile una nueva al Colegio."
)


def validate_new_password(raw: str) -> str:
    """Normaliza y valida una contraseña nueva. Lanza ValueError si no cumple el mínimo."""
    pwd = (raw or "").strip()
    if len(pwd) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres")
    return pwd

def hash_password(plain: str) -> str:
    return _pwd.hash((plain or "").strip())

def verify_password(plain: str, hashed: Optional[str]) -> bool:
    try:
        return _pwd.verify((plain or "").strip(), (hashed or "").strip())
    except Exception:
        return False

def needs_update(hashed: Optional[str]) -> bool:
    try:
        return _pwd.needs_update((hashed or "").strip())
    except Exception:
        return True  # si es raro/desconocido, forzamos migración

def hash_password_inicial() -> str:
    """Hash de `PASSWORD_INICIAL`. Único punto de alta de credenciales."""
    return hash_password(PASSWORD_INICIAL)


def generar_password_provisoria() -> str:
    """Contraseña aleatoria de 8 caracteres en dos bloques, p. ej. `k7mp-2qxa`.

    31 símbolos posibles por carácter: ~40 bits. Alcanza de sobra con el rate
    limit del login (8 intentos fallidos por cuenta cada 15 minutos) y con que
    vence a las `HORAS_PASSWORD_PROVISORIA`.
    """
    def bloque() -> str:
        return "".join(secrets.choice(_ALFABETO_PROVISORIA) for _ in range(4))

    return f"{bloque()}-{bloque()}"


def _ahora() -> datetime:
    """UTC naive, como el resto de las columnas DATETIME de la base."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def vencimiento_password_provisoria() -> datetime:
    return _ahora() + timedelta(hours=HORAS_PASSWORD_PROVISORIA)


def tiene_password_provisoria(user: ListadoMedico) -> bool:
    """La contraseña actual la generó "Renovar contraseña" y todavía no se cambió."""
    return user.password_provisoria_vence is not None


def password_provisoria_vencida(user: ListadoMedico) -> bool:
    vence = user.password_provisoria_vence
    return vence is not None and vence <= _ahora()


async def verify_and_upgrade(
    db: AsyncSession,
    user: ListadoMedico,
    plain: str,
) -> bool:
    """Valida la contraseña contra el hash guardado y migra el hash si quedó viejo.

    Sin fallbacks: si el hash no coincide, no entra. Ver la nota de A2 arriba.
    """
    pwd = (plain or "").strip()
    stored = (user.hashed_password or "").strip()

    if not stored or not verify_password(pwd, stored):
        return False

    # Verificó: si el hash está deprecado/antiguo → migrar a pbkdf2.
    if needs_update(stored):
        user.hashed_password = hash_password(pwd)
        db.add(user)
        await db.commit()
    return True