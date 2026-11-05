"""CBU y medio de cobro del socio.

`listado_medico` guarda dos cuentas y cuál se usa para pagarle:

    cbu_santander   CBU de una cuenta del Banco Santander (entidad 072)
    cbu_otro_banco  CBU de una cuenta de cualquier otro banco
    medio_cobro     'santander' | 'otros_bancos' | 'cheque'

Este módulo es el único lugar donde se decide qué es un CBU válido. Lo usan el
legajo, el alta/registro, el informe de pago y el script de migración.

Un CBU son 22 dígitos: entidad (3) + sucursal (4) + dígito verificador del primer
bloque (1) + cuenta (13) + dígito verificador del segundo bloque (1). Los dos
dígitos verificadores se calculan con el algoritmo del BCRA (módulo 10).

Valores viejos que no son un CBU (la cuenta HSBC de 10 dígitos del sistema anterior,
un CUIT cargado por error) se conservan en `cbu_otro_banco` con medio `cheque`: no se
borran, pero tampoco se pueden elegir como medio de cobro ni se exportan a un banco.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

MedioCobro = Literal["santander", "otros_bancos", "cheque"]
MEDIOS_COBRO: tuple[str, ...] = ("santander", "otros_bancos", "cheque")
ENTIDAD_SANTANDER = "072"

_PESOS_BLOQUE_1 = (7, 1, 3, 9, 7, 1, 3)
_PESOS_BLOQUE_2 = (3, 9, 7, 1, 3, 9, 7, 1, 3, 9, 7, 1, 3)


class DatosBancariosInvalidos(ValueError):
    """Los datos bancarios no se pueden guardar. El mensaje es para el usuario."""


def solo_digitos(valor: object) -> str:
    """Deja solo los dígitos (la gente pega el CBU con espacios o guiones)."""
    return re.sub(r"\D", "", "" if valor is None else str(valor))


def _digito(pesos: tuple[int, ...], digitos: str) -> int:
    return (10 - sum(int(d) * p for d, p in zip(digitos, pesos)) % 10) % 10


def es_cbu_valido(valor: object) -> bool:
    """22 dígitos y los dos dígitos verificadores correctos."""
    s = "" if valor is None else str(valor).strip()
    if len(s) != 22 or not s.isdigit():
        return False
    return (
        _digito(_PESOS_BLOQUE_1, s[:7]) == int(s[7])
        and _digito(_PESOS_BLOQUE_2, s[8:21]) == int(s[21])
    )


def es_santander(valor: object) -> bool:
    return str(valor or "").strip().startswith(ENTIDAD_SANTANDER)


def entidad(valor: object) -> Optional[str]:
    s = str(valor or "").strip()
    return s[:3] if len(s) >= 3 else None


def es_cuit_valido(valor: object) -> bool:
    """11 dígitos y dígito verificador (módulo 11)."""
    s = solo_digitos(valor)
    if len(s) != 11:
        return False
    pesos = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
    resto = 11 - sum(int(d) * p for d, p in zip(s, pesos)) % 11
    dv = 0 if resto == 11 else 9 if resto == 10 else resto
    return dv == int(s[10])


def _limpio(valor: object) -> Optional[str]:
    """Texto sin espacios; vacío o '0' equivalen a «sin dato»."""
    s = "" if valor is None else str(valor).strip()
    return None if s in ("", "0") else s


def _normalizar_santander(valor: object) -> Optional[str]:
    s = _limpio(valor)
    if s is None:
        return None
    d = solo_digitos(s)
    if not es_cbu_valido(d):
        raise DatosBancariosInvalidos(
            "El CBU de Santander no es válido: tiene que tener 22 dígitos y el dígito verificador correcto."
        )
    if not es_santander(d):
        raise DatosBancariosInvalidos(
            "El CBU cargado como Santander no es del Banco Santander (tiene que empezar con 072). "
            "Cargalo en «Otro banco»."
        )
    return d


def _normalizar_otro_banco(valor: object) -> Optional[str]:
    s = _limpio(valor)
    if s is None:
        return None
    d = solo_digitos(s)
    if not es_cbu_valido(d):
        raise DatosBancariosInvalidos(
            "El CBU de otro banco no es válido: tiene que tener 22 dígitos y el dígito verificador correcto."
        )
    if es_santander(d):
        raise DatosBancariosInvalidos(
            "El CBU cargado como «otro banco» es del Banco Santander (empieza con 072). Cargalo en «Santander»."
        )
    return d


def _medio_valido(medio: object) -> Optional[str]:
    if medio is None or str(medio).strip() == "":
        return None
    m = str(medio).strip()
    if m not in MEDIOS_COBRO:
        raise DatosBancariosInvalidos("El medio de cobro tiene que ser Santander, Otros bancos o Cheque.")
    return m


def _validar_medio(medio: str, cbu_santander: Optional[str], cbu_otro_banco: Optional[str]) -> None:
    if medio == "santander" and not (cbu_santander and es_cbu_valido(cbu_santander)):
        raise DatosBancariosInvalidos("Para cobrar por Santander hay que cargar un CBU de Santander válido.")
    if medio == "otros_bancos" and not (cbu_otro_banco and es_cbu_valido(cbu_otro_banco)):
        raise DatosBancariosInvalidos(
            "Para cobrar por otro banco hay que cargar un CBU válido de ese banco "
            "(el valor guardado no es un CBU de 22 dígitos)."
            if cbu_otro_banco
            else "Para cobrar por otro banco hay que cargar el CBU de ese banco."
        )


def resolver_alta(
    *,
    cbu_santander: object = None,
    cbu_otro_banco: object = None,
    medio_cobro: object = None,
    cbu: object = None,
) -> dict:
    """Datos bancarios de un socio NUEVO (registro público o alta administrativa).

    `cbu` es el campo único de los clientes viejos: se clasifica por la entidad (072 →
    Santander, otra → otro banco). Si no llega nada, el socio cobra por cheque.
    Devuelve las tres columnas ya normalizadas y lanza `DatosBancariosInvalidos` si
    algo no cierra.
    """
    santander = _normalizar_santander(cbu_santander)
    otro = _normalizar_otro_banco(cbu_otro_banco)

    unico = _limpio(cbu)
    if unico is not None and santander is None and otro is None:
        d = solo_digitos(unico)
        if not es_cbu_valido(d):
            raise DatosBancariosInvalidos("El CBU no es válido: tiene que tener 22 dígitos y el dígito verificador correcto.")
        if es_santander(d):
            santander = d
        else:
            otro = d

    medio = _medio_valido(medio_cobro)
    if medio is None:
        medio = "santander" if santander else "otros_bancos" if otro else "cheque"
    _validar_medio(medio, santander, otro)
    return {"cbu_santander": santander, "cbu_otro_banco": otro, "medio_cobro": medio}


def resolver_cambio(actual: dict, cambios: dict) -> dict:
    """Datos bancarios al EDITAR un socio. `actual` y `cambios` usan las 3 claves.

    Devuelve solo lo que hay que escribir (vacío si no cambia nada). Un valor que ya
    estaba guardado se acepta como está aunque no sea un CBU (los 264 valores viejos
    de la migración): lo que se valida es lo que el usuario escribe.
    """
    claves = ("cbu_santander", "cbu_otro_banco", "medio_cobro")
    nuevo = {k: actual.get(k) for k in claves}
    escribir: dict = {}

    if "cbu_santander" in cambios:
        v = _limpio(cambios["cbu_santander"])
        if v != _limpio(actual.get("cbu_santander")):
            v = _normalizar_santander(v)
        else:
            v = _limpio(actual.get("cbu_santander"))
        nuevo["cbu_santander"] = v
    if "cbu_otro_banco" in cambios:
        v = _limpio(cambios["cbu_otro_banco"])
        if v != _limpio(actual.get("cbu_otro_banco")):
            v = _normalizar_otro_banco(v)
        else:
            v = _limpio(actual.get("cbu_otro_banco"))
        nuevo["cbu_otro_banco"] = v
    if "medio_cobro" in cambios:
        nuevo["medio_cobro"] = _medio_valido(cambios["medio_cobro"]) or actual.get("medio_cobro") or "cheque"

    for k in claves:
        if nuevo[k] != (_limpio(actual.get(k)) if k != "medio_cobro" else actual.get(k)):
            escribir[k] = nuevo[k]
    if not escribir:
        return {}

    # Si el medio de cobro queda apuntando a una cuenta que no sirve, no se guarda.
    _validar_medio(nuevo["medio_cobro"] or "cheque", nuevo["cbu_santander"], nuevo["cbu_otro_banco"])
    return escribir
