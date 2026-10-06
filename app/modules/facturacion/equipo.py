"""Equipo quirúrgico sin vínculo explícito: qué ayudante/pediatra va con qué médico de cabecera.

Lo normal es que el ayudante o el pediatra traiga `grupo_equipo_id` (apunta a la cabeza).
Pero hay ayudantes y pediatras cargados sueltos, sin grupo: de todas formas van con su
médico de cabecera, así que se lo busca entre las prestaciones de la misma factura. Es la
misma regla que aplica el listado del front (`FacturaDetalle/equipo.ts`) — si se cambia una,
se cambia la otra. No se graba nada: el vínculo se calcula al armar la vista o el exportable.
"""
from dataclasses import dataclass
from typing import Optional

from app.modules.facturacion.service import CODIGOS_CON_PEDIATRA, TIPO_PRESTADOR_PEDIATRA


@dataclass(frozen=True)
class CandidatoEquipo:
    id: int
    cod_medico: str
    cod_clinica: Optional[int]
    codigo: Optional[str]
    # "Medico" | "Ayudante" | "Gastos" | "Pediatra" | None (ver `_derivar_tipo_prestador`).
    tipo_prestador: Optional[str]
    paciente: str  # `clave_paciente`
    fecha_practica: object  # date | None
    grupo_equipo_id: Optional[int]


def normal(s: object) -> str:
    return " ".join(str(s or "").split()).casefold()


def clave_paciente(nro_afiliado: object, nombre: object) -> str:
    """El paciente se identifica por su documento/afiliado; sin eso, por el nombre."""
    return normal(nro_afiliado) or normal(nombre)


def _es_integrante_suelto(c: CandidatoEquipo) -> bool:
    return c.grupo_equipo_id is None and c.tipo_prestador in ("Ayudante", TIPO_PRESTADOR_PEDIATRA)


def _puede_ser_cabeza(c: CandidatoEquipo) -> bool:
    return c.grupo_equipo_id == c.id or (
        c.grupo_equipo_id is None and c.tipo_prestador not in ("Ayudante", TIPO_PRESTADOR_PEDIATRA, "Gastos")
    )


def inferir_equipos(candidatos: list[CandidatoEquipo]) -> dict[int, int]:
    """Para cada ayudante/pediatra SIN grupo, la cabeza que le corresponde (id integrante →
    id cabeza): misma factura, mismo paciente y misma fecha de práctica, de otro socio. Si
    hay varias, gana la que ya es cabeza de un equipo, después la misma clínica y el mismo
    código, y por último la de id más cercano (anterior primero). Sin candidata, queda suelto."""
    por_clave: dict[tuple, list[CandidatoEquipo]] = {}
    for c in candidatos:
        if _puede_ser_cabeza(c):
            por_clave.setdefault((c.paciente, c.fecha_practica), []).append(c)

    out: dict[int, int] = {}
    for m in candidatos:
        if not _es_integrante_suelto(m) or not m.paciente:
            continue
        cands = [
            h for h in por_clave.get((m.paciente, m.fecha_practica), [])
            if h.id != m.id and h.cod_medico != m.cod_medico
        ]
        if not cands:
            continue

        def puntaje(h: CandidatoEquipo) -> int:
            s = 0
            if h.grupo_equipo_id == h.id:
                s += 4
            if h.cod_clinica == m.cod_clinica:
                s += 2
            mismo_codigo = h.codigo is not None and h.codigo == m.codigo
            habilita_pediatra = m.tipo_prestador == TIPO_PRESTADOR_PEDIATRA and h.codigo in CODIGOS_CON_PEDIATRA
            if mismo_codigo or habilita_pediatra:
                s += 2
            return s

        def distancia(h: CandidatoEquipo) -> int:
            return m.id - h.id if h.id < m.id else (h.id - m.id) * 1_000_000

        out[m.id] = min(cands, key=lambda h: (-puntaje(h), distancia(h))).id
    return out
