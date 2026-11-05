"""Búsqueda de socios: UN solo criterio de coincidencia y de orden para todos los buscadores de liquidación y pagos
(selector de médicos, lotes, adelantos, compensaciones, deducciones, cobranzas, vista de la factura).

Qué se busca según lo que se escribe:
  - Sólo números  -> primero el N° DE SOCIO, después el NOMBRE y por último la MATRÍCULA.
  - Con letras    -> el NOMBRE y por último la MATRÍCULA (el N° de socio no entra: un texto no es un número).

En cada campo, primero lo EXACTO y después lo parcial (empieza con lo escrito y, al final, lo contiene). No distingue
mayúsculas ni acentos (lo resuelve la collation de `NOMBRE`, utf8_general_ci). Ejemplo, escribir «3»:
  1. el socio Nro 3
  2. los N° de socio que empiezan con 3 (30, 31, 300…, del más chico al más grande) y después los que lo contienen (13, 23…)
  3. los nombres que tengan un 3
  4. la matrícula 3 y, al final, las que empiezan con 3 o lo contienen

Uso: `c = criterio_socio(texto)`; si `c` no es None, `query.where(c.condicion).order_by(*c.orden)`. Para otras tablas
con el médico unido por alias, se pasan las columnas (`nro=`, `nombre=`, `matricula=`).
"""
from __future__ import annotations

from typing import NamedTuple, Optional

from sqlalchemy import String, and_, case, cast, or_
from sqlalchemy.sql.elements import ColumnElement

from app.db.models.medico import ListadoMedico


class CriterioSocio(NamedTuple):
    condicion: ColumnElement  # qué socios coinciden
    rango: ColumnElement  # 0 = lo más exacto … cuanto más grande, más lejos
    orden: list  # lo que va en ORDER BY: rango, N° de socio (si se buscó un número) y nombre
    por_numero: bool = False  # se escribió sólo un número (para desempatar a mano en listados agrupados)


def solo_numeros(texto: str) -> bool:
    return texto.isascii() and texto.isdigit()


def criterio_socio(
    texto: Optional[str],
    *,
    nro=ListadoMedico.NRO_SOCIO,
    nombre=ListadoMedico.NOMBRE,
    matricula=ListadoMedico.MATRICULA_PROV,
) -> Optional[CriterioSocio]:
    """El criterio de búsqueda para ese texto, o None si no hay nada que buscar (texto vacío)."""
    t = " ".join((texto or "").split())
    if not t:
        return None

    mat = cast(matricula, String)

    if solo_numeros(t):
        nro_s = cast(nro, String)
        condicion = or_(
            nro_s.contains(t, autoescape=True),
            nombre.contains(t, autoescape=True),
            mat.contains(t, autoescape=True),
        )
        rango = case(
            (nro_s == t, 0),
            (nro_s.startswith(t, autoescape=True), 1),
            (nro_s.contains(t, autoescape=True), 2),
            (nombre == t, 3),
            (nombre.startswith(t, autoescape=True), 4),
            (nombre.contains(t, autoescape=True), 5),
            (mat == t, 6),
            (mat.startswith(t, autoescape=True), 7),
            else_=8,
        )
        # Entre los N° de socio que coinciden, el más chico primero (3, 30, 31, 300…).
        return CriterioSocio(condicion, rango, [rango.asc(), nro.asc(), nombre.asc()], True)

    todas = and_(*[nombre.contains(p, autoescape=True) for p in t.split()])
    condicion = or_(todas, mat.contains(t, autoescape=True))
    rango = case(
        (nombre == t, 0),
        (nombre.startswith(t, autoescape=True), 1),
        (nombre.contains(t, autoescape=True), 2),
        (todas, 3),  # todas las palabras, en cualquier orden («perez juan» encuentra «PEREZ DE JUAN»)
        (mat == t, 4),
        (mat.startswith(t, autoescape=True), 5),
        else_=6,
    )
    return CriterioSocio(condicion, rango, [rango.asc(), nombre.asc()])
