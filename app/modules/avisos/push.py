"""
Despacho de notificaciones push vía Expo Push Service.

Manda el aviso ya persistido a los dispositivos registrados (dispositivos_push)
y deja el resultado en las columnas push_estado / push_error / destinatarios del
propio aviso.

Notas de la API de Expo (https://docs.expo.dev/push-notifications/sending-notifications/):
  - hasta 100 mensajes por request
  - tope de 600 notificaciones por segundo por proyecto
  - la respuesta trae un ticket por mensaje; un ticket "error" con
    DeviceNotRegistered significa que ese token murió y hay que darlo de baja

El servicio de Expo es gratuito y no requiere plan pago. EXPO_ACCESS_TOKEN es
opcional pero recomendado: con "Enhanced Security for Push Notifications"
activado en expo.dev, sin ese header nadie puede mandar pushes al proyecto
aunque haya conseguido un token de dispositivo.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable, Optional, Sequence

import httpx

from config import settings

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"

# Tope de la API de Expo.
CHUNK_SIZE = 100
# 6 requests en vuelo × 100 mensajes = 600/s, justo el límite del proyecto.
MAX_CONCURRENCY = 6
REQUEST_TIMEOUT = 30.0

# El motivo por el que un token deja de servir: el usuario desinstaló el app o
# revocó los permisos. Se desactiva para no volver a intentarlo.
DEVICE_NOT_REGISTERED = "DeviceNotRegistered"


class PushResult:
    """Qué pasó con el despacho, para volcarlo en el aviso."""

    def __init__(self) -> None:
        self.enviados: int = 0
        self.fallidos: int = 0
        self.tokens_muertos: list[str] = []
        self.error: Optional[str] = None

    @property
    def estado(self) -> str:
        if self.error and self.enviados == 0:
            return "error"
        return "enviado"


def _chunks(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        # Expo comprime la respuesta si se lo pedís; con 100 tickets ayuda.
        "Accept-Encoding": "gzip, deflate",
    }
    token = getattr(settings, "EXPO_ACCESS_TOKEN", None)
    if token:
        secret = token.get_secret_value() if hasattr(token, "get_secret_value") else token
        headers["Authorization"] = f"Bearer {secret}"
    return headers


def _mensajes(tokens: Sequence[str], *, titulo: str, cuerpo: str, data: dict) -> list[dict]:
    """Un mensaje por token.

    Sólo viajan titulo/mensaje y el id del aviso: el payload de una push no es
    un canal confiable ni cifrado extremo a extremo, así que no se mandan datos
    personales del socio. El app abre /avisos y lee el detalle autenticado.
    """
    return [
        {
            "to": token,
            "title": titulo,
            "body": cuerpo,
            "sound": "default",
            "priority": "high",
            "channelId": "avisos",
            "data": data,
        }
        for token in tokens
    ]


async def _enviar_chunk(
    client: httpx.AsyncClient,
    chunk: Sequence[str],
    *,
    titulo: str,
    cuerpo: str,
    data: dict,
    result: PushResult,
    lock: asyncio.Lock,
) -> None:
    payload = _mensajes(chunk, titulo=titulo, cuerpo=cuerpo, data=data)
    try:
        response = await client.post(EXPO_PUSH_URL, json=payload, headers=_headers())
        response.raise_for_status()
        tickets: list[dict[str, Any]] = (response.json() or {}).get("data") or []
    except Exception as exc:  # noqa: BLE001 — un chunk caído no debe cortar el resto
        logger.warning("push: chunk de %d falló: %s", len(chunk), exc)
        async with lock:
            result.fallidos += len(chunk)
            result.error = result.error or str(exc)[:200]
        return

    async with lock:
        # Los tickets vuelven en el mismo orden que los mensajes enviados.
        for token, ticket in zip(chunk, tickets):
            if ticket.get("status") == "ok":
                result.enviados += 1
                continue
            result.fallidos += 1
            detalle = ticket.get("details") or {}
            if detalle.get("error") == DEVICE_NOT_REGISTERED:
                result.tokens_muertos.append(token)
            else:
                result.error = result.error or (ticket.get("message") or "")[:200]


async def enviar(
    tokens: Sequence[str],
    *,
    titulo: str,
    cuerpo: str,
    aviso_id: int,
) -> PushResult:
    """Despacha a todos los tokens en tandas paralelas acotadas.

    No levanta excepciones: el aviso ya está publicado y visible en el app, así
    que un fallo del proveedor se registra pero no rompe la operación.
    """
    result = PushResult()
    if not tokens:
        return result

    data = {"tipo": "aviso", "aviso_id": aviso_id, "url": "/avisos"}
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:

        async def run(chunk: Sequence[str]) -> None:
            async with semaphore:
                await _enviar_chunk(
                    client,
                    chunk,
                    titulo=titulo,
                    cuerpo=cuerpo,
                    data=data,
                    result=result,
                    lock=lock,
                )

        await asyncio.gather(*(run(c) for c in _chunks(tokens, CHUNK_SIZE)))

    return result
