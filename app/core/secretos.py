"""Cifrado simétrico para secretos que la aplicación necesita poder mostrar.

Contradice la regla de "las contraseñas se hashean", y a propósito: esa regla es
para credenciales que la aplicación **verifica**. Acá son las claves de las
casillas de correo del Colegio, que un administrativo tiene que **leer** para
configurar Outlook. Un hash las volvería inútiles, y la alternativa real no es
hashearlas sino dejarlas en el Excel compartido donde están hoy.

El diseño asume que la base puede filtrarse: la llave sale de `SECRETOS_KEY`
(variable de entorno), no de la base. Sin llave, `cifrar()` levanta y el endpoint
responde 503 — nunca degrada a texto plano.

Fernet porque es autenticado: un token manipulado falla al descifrar en vez de
devolver basura.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class SecretoNoDisponible(RuntimeError):
    """No hay `SECRETOS_KEY`, así que no se puede cifrar ni descifrar."""


def _fernet() -> Fernet:
    """El cifrador, derivando la llave de `SECRETOS_KEY`.

    La derivación es SHA-256 sobre el valor configurado, no un uso directo: así
    `SECRETOS_KEY` puede ser cualquier string (una passphrase larga) y no
    obligatoriamente los 32 bytes en base64 que Fernet exige. Es determinística
    y sin sal — la sal haría falta si esto protegiera contra un ataque de
    diccionario sobre una contraseña humana, y acá el insumo es un secreto de
    infraestructura de alta entropía, no una contraseña.

    **Cambiar `SECRETOS_KEY` deja ilegible todo lo ya guardado.** No hay
    rotación automática: si hay que rotarla, primero se leen las contraseñas con
    la llave vieja y se vuelven a guardar con la nueva.
    """
    crudo = settings.SECRETOS_KEY
    valor = crudo.get_secret_value().strip() if crudo else ""
    if not valor:
        raise SecretoNoDisponible(
            "Falta SECRETOS_KEY en la configuración del servidor: sin esa "
            "variable no se pueden guardar ni leer contraseñas de correo."
        )
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(valor.encode()).digest()))


def cifrado_disponible() -> bool:
    """Si el servidor está en condiciones de guardar secretos.

    Lo usa el `GET` para avisarle a la pantalla que la función está apagada,
    antes de que alguien escriba una contraseña y reciba un 503.
    """
    crudo = settings.SECRETOS_KEY
    return bool(crudo and crudo.get_secret_value().strip())


def cifrar(texto: str) -> str:
    """Texto plano → token Fernet listo para guardar."""
    return _fernet().encrypt(texto.encode()).decode()


def descifrar(token: str) -> str:
    """Token guardado → texto plano.

    `InvalidToken` se traduce a un mensaje que dice qué pasó de verdad: el caso
    real no es "el dato está corrupto" sino "esto se cifró con otra llave",
    típicamente después de reinstalar el servidor con un `.env` nuevo.
    """
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError(
            "La contraseña guardada no se puede descifrar con la SECRETOS_KEY "
            "actual. Se guardó con otra llave: hay que volver a cargarla."
        ) from exc
