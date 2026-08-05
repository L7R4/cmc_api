"""Rate limiting para los endpoints de autenticación.

Por qué hace falta acá y no en otro lado: el espacio de contraseñas de esta app
es chico y conocido. La matrícula tiene 4-5 dígitos (~90.000 valores) y es dato
de registro público; el DNI inicial tiene 8. Sin límite, probar las 90.000
matrículas contra una cuenta a 100 req/s toma unos 15 minutos. Peor todavía es
el camino inverso — *password spraying*: probar una matrícula real contra los
~4.500 socios son 4.500 requests, menos de un minuto.

Se cuentan dos claves y hacen falta las dos:

  - **por IP**       frena a quien golpea desde una máquina
  - **por nro_socio** frena a quien rota IPs; es el que corta el spraying,
                      porque la cuenta objetivo acumula en un solo contador

Tres decisiones que evitan bloquear al usuario legítimo:

  1. Solo se cuentan los **fallos**. Si se contaran los éxitos, el médico que
     usa la app y renueva seguido se auto-bloquearía.
  2. Un login exitoso **resetea** el contador de ese socio.
  3. La respuesta es 429 con `Retry-After`, para que el front pueda decir
     "reintentá en N minutos" en vez de "credenciales inválidas".

⚠️ **Alcance: este contador vive en memoria del proceso.** Con N workers de
Uvicorn el límite efectivo es N veces el configurado (con 4 workers, 20 intentos
cada 15 min en vez de 5). Sigue siendo la diferencia entre "ilimitado" y "meses
de fuerza bruta", pero cuando haya Redis conviene mover `_Contador` allá:
la interfaz (`registrar_fallo` / `limpiar` / `chequear`) está pensada para eso.
Ver `docs/api/AUDITORIA_SEGURIDAD.md` §B3.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.core.config import settings

logger = logging.getLogger(__name__)


class _Contador:
    """Ventana deslizante: guarda los timestamps de los fallos por clave."""

    def __init__(self) -> None:
        self._eventos: dict[str, deque[float]] = defaultdict(deque)

    def _podar(self, clave: str, ventana: int, ahora: float) -> deque[float]:
        cola = self._eventos[clave]
        limite = ahora - ventana
        while cola and cola[0] < limite:
            cola.popleft()
        return cola

    def contar(self, clave: str, ventana: int) -> int:
        return len(self._podar(clave, ventana, time.monotonic()))

    def registrar(self, clave: str, ventana: int) -> int:
        ahora = time.monotonic()
        cola = self._podar(clave, ventana, ahora)
        cola.append(ahora)
        return len(cola)

    def segundos_para_liberar(self, clave: str, ventana: int) -> int:
        cola = self._eventos.get(clave)
        if not cola:
            return 0
        return max(1, int(ventana - (time.monotonic() - cola[0])))

    def limpiar(self, clave: str) -> None:
        self._eventos.pop(clave, None)

    def podar_todo(self, ventana: int) -> None:
        """Descarta claves sin eventos vigentes. Evita que el dict crezca sin
        techo cuando el atacante rota IPs: sin esto, cada IP distinta deja una
        entrada viva para siempre."""
        ahora = time.monotonic()
        for clave in list(self._eventos):
            if not self._podar(clave, ventana, ahora):
                del self._eventos[clave]


_fallos = _Contador()
_peticiones_desde_poda = 0
_PODA_CADA = 500


def _ip(request: Request) -> str:
    # Detrás de Caddy el cliente real viene en X-Forwarded-For. Se toma el
    # primer valor, que es el cliente original.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "desconocido"


def _rechazar(clave: str, ventana: int, motivo: str) -> None:
    espera = _fallos.segundos_para_liberar(clave, ventana)
    minutos = max(1, round(espera / 60))
    logger.warning("rate limit: %s (clave=%s, libera en %ss)", motivo, clave, espera)
    raise HTTPException(
        status_code=429,
        detail=f"Demasiados intentos fallidos. Reintentá en {minutos} minuto(s).",
        headers={"Retry-After": str(espera)},
    )


def chequear_login(request: Request, nro_socio: int | None) -> None:
    """Se llama ANTES de validar credenciales. Corta si ya se pasó del límite."""
    global _peticiones_desde_poda

    if not settings.RATE_LIMIT_ENABLED:
        return

    ventana = settings.RATE_LIMIT_WINDOW_SECONDS

    _peticiones_desde_poda += 1
    if _peticiones_desde_poda >= _PODA_CADA:
        _peticiones_desde_poda = 0
        _fallos.podar_todo(ventana)

    clave_ip = f"ip:{_ip(request)}"
    if _fallos.contar(clave_ip, ventana) >= settings.RATE_LIMIT_MAX_PER_IP:
        _rechazar(clave_ip, ventana, "demasiados fallos desde una IP")

    if nro_socio is not None:
        clave_socio = f"socio:{nro_socio}"
        if _fallos.contar(clave_socio, ventana) >= settings.RATE_LIMIT_MAX_PER_SOCIO:
            _rechazar(clave_socio, ventana, "demasiados fallos contra una cuenta")


def registrar_fallo(request: Request, nro_socio: int | None) -> None:
    """Se llama cuando las credenciales resultaron inválidas."""
    if not settings.RATE_LIMIT_ENABLED:
        return
    ventana = settings.RATE_LIMIT_WINDOW_SECONDS
    _fallos.registrar(f"ip:{_ip(request)}", ventana)
    if nro_socio is not None:
        _fallos.registrar(f"socio:{nro_socio}", ventana)


def registrar_exito(request: Request, nro_socio: int | None) -> None:
    """Login válido: se limpia el contador de esa cuenta.

    La IP no se limpia a propósito: si desde una misma IP entran 40 cuentas
    distintas, cada login exitoso borraría la evidencia del ataque.
    """
    if not settings.RATE_LIMIT_ENABLED:
        return
    if nro_socio is not None:
        _fallos.limpiar(f"socio:{nro_socio}")
