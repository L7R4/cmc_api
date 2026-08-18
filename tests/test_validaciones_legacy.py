"""Espejo del panel nuevo en `guardar_atencion` (el puente temporal con el legacy).

Lo que se protege acá es lo que rompe la facturación del sistema viejo si sale
mal, y que no se nota mirando la pantalla del médico: la prestación se ve
perfecta del lado nuevo y desaparece —o se factura en el bloque equivocado— del
lado viejo.

Los tests son de mapeo puro: arman objetos en memoria y no tocan la base, salvo
el que compara el registro de obras sociales contra el de perfiles.
"""
import datetime
from decimal import Decimal

import pytest

from app.db.models import DetalleFacturacionCMC, ListadoMedico
from app.modules.validaciones import obras
from app.modules.validaciones.legacy import mapeo
from app.modules.validaciones.legacy.perfiles import PERFILES, perfil_de


def _detalle(**overrides) -> DetalleFacturacionCMC:
    base = dict(
        id_detalle_prestaciones=1,
        periodo="202608",
        cod_obr="411",
        cod_med="1084",
        cod_nom="420351",
        cantidad=1,
        honorarios=Decimal("26000.00"),
        gastos=Decimal("0.00"),
        coseguro=Decimal("0.00"),
        importe_total=Decimal("26000.00"),
        dni_p="2012871/00",
        nom_ape_p="TARON PAOLA",
        fecha_practica=datetime.date(2026, 8, 12),
        autorizacion="131705017",
        usuario="1084",
        validacion_estado="autorizada",
        validacion_detalle="AUTORIZADA",
    )
    base.update(overrides)
    return DetalleFacturacionCMC(**base)


def _medico(**overrides) -> ListadoMedico:
    base = dict(
        NRO_SOCIO=1084,
        NOMBRE="PEREZ JUAN",
        MATRICULA_PROV=1827,
        NRO_ESPECIALIDAD=44,
        CATEGORIA="C",
    )
    base.update(overrides)
    return ListadoMedico(**base)


def _construir(detalle=None, medico=None, con_hono_sana="C"):
    detalle = detalle or _detalle()
    return mapeo.construir_fila(
        detalle=detalle,
        medico=medico or _medico(),
        perfil=perfil_de(int(detalle.cod_obr)),
        con_hono_sana=con_hono_sana,
        hoy=datetime.date(2026, 8, 13),
    )


def test_toda_obra_social_registrada_tiene_perfil_legacy():
    """Sumar una obra social a `obras/` y olvidarse del perfil no rompe nada en
    el momento —el espejo se saltea y avisa— pero sus prestaciones dejan de
    llegar a la facturación del legacy sin que nadie se entere. Este test es el
    aviso temprano.
    """
    sin_perfil = [(v.nro, v.nombre) for v in obras.VALIDADORES if perfil_de(v.nro) is None]
    assert not sin_perfil, (
        "Estas obras sociales están en obras/ pero no en legacy/perfiles.py, "
        f"así que no se espejan: {sin_perfil}"
    )


def test_nobis_es_62():
    """Nobis estuvo registrada como 402, que no existe en `obras_sociales` ni en
    ninguna tabla de precios: con ese número el lookup no encontraba nada y toda
    carga de Nobis moría en 422. El número real es 62.
    """
    nobis = [v for v in obras.VALIDADORES if v.nombre.startswith("Nobis")]
    assert [v.nro for v in nobis] == [62]
    assert 402 not in PERFILES


def test_el_espejo_copia_la_obra_social_sin_traducirla():
    """No hay mapeo de números entre los dos sistemas, y no tiene que haberlo:
    una traducción es la forma de que una prestación termine facturada en una
    obra social que no es la suya.
    """
    for nro in PERFILES:
        fila = _construir(_detalle(cod_obr=str(nro)))
        assert fila.NRO_OBRA_SOCIAL == nro


def test_valor_cirujia_es_el_importe_total_del_sistema_nuevo():
    """`SUM(VALOR_CIRUJIA)` es la única suma de plata del legacy (17 consultas).
    Tiene que dar lo mismo que factura el sistema nuevo.
    """
    fila = _construir()
    assert fila.VALOR_CIRUJIA == Decimal("26000.00")


def test_valor_cirujia_descuenta_el_coseguro():
    """Boreal: el afiliado ya pagó el coseguro, a la obra social se le factura el
    neto. Es la fórmula del legacy —(honorarios + gastos) − coseguro— y ya viene
    resuelta en `importe_total`.
    """
    fila = _construir(
        _detalle(
            cod_obr="285",
            honorarios=Decimal("17000.00"),
            gastos=Decimal("0.00"),
            coseguro=Decimal("4900.00"),
            importe_total=Decimal("12100.00"),
        )
    )
    assert fila.IMPORTE_COLEGIO == Decimal("17000.00")
    assert fila.COSEGURO == Decimal("4900.00")
    assert fila.VALOR_CIRUJIA == Decimal("12100.00")


def test_prestacion_no_autorizada_va_sin_numero_de_autorizacion():
    """Varias consultas del legacy filtran `NRO_CONSULTA <> 0`. Una prestación
    que la obra social no autorizó tiene que quedar afuera de esas listas, igual
    que queda afuera de la factura nueva con `estado='X'`.
    """
    fila = _construir(
        _detalle(
            autorizacion=None,
            validacion_estado="rechazada",
            validacion_detalle="PRESTACIONES RECHAZADAS",
            honorarios=Decimal("0.00"),
            gastos=Decimal("0.00"),
            importe_total=Decimal("0.00"),
        )
    )
    assert fila.NRO_CONSULTA == "0"
    assert fila.VALOR_CIRUJIA == Decimal("0.00")


def test_las_claves_de_agrupacion_salen_del_medico_y_del_codigo():
    """`CATEGORIA_A_B_C` y `CON_HONO_SANA` deciden en qué bloque de la
    facturación del legacy cae la prestación.
    """
    fila = _construir(medico=_medico(CATEGORIA="B"), con_hono_sana="P")
    assert fila.CATEGORIA_A_B_C == "B"
    assert fila.CON_HONO_SANA == "P"


def test_el_periodo_se_parte_en_mes_y_anio():
    fila = _construir(_detalle(periodo="202601"))
    assert (fila.MES_PERIODO, fila.ANIO_PERIODO) == (1, 2026)


def test_la_fila_nace_visible_y_arrastrable():
    """`EXISTE='S'` porque casi todo el legacy filtra por eso, y `RESULTADO='N'`
    para que el paso de período la arrastre como a cualquier otra (mueve las que
    cumplen `RESULTADO='N'` / `RESULTADO<>'S'`).
    """
    fila = _construir()
    assert fila.EXISTE == "S"
    assert fila.RESULTADO == "N"


@pytest.mark.parametrize("nro", sorted(PERFILES))
def test_ningun_campo_de_texto_excede_el_ancho_de_su_columna(nro):
    """El legacy no valida largos: un `INSERT` que se pasa termina en dato
    truncado o en error, según el modo de MySQL.
    """
    anchos = {
        "CODIGO_PRESTACION": 8,
        "NOMBRE_PRESTADOR": 40,
        "ESTADODESCRIPCION": 100,
        "MENSAJE": 5,
        "NOMBRE_AFILIADO": 40,
        "NRO_AFILIADO": 20,
        "NRO_CONSULTA": 16,
        "RESULTADO": 5,
        "FECHASUSPENSION": 10,
        "EXISTE": 1,
        "NOMBRE_ARCHIVO": 100,
        "CATEGORIA_A_B_C": 1,
        "SANATORIO": 50,
        "PACIENTE": 50,
        "CON_HONO_SANA": 1,
    }
    largo = "X" * 300
    fila = _construir(
        _detalle(
            cod_obr=str(nro),
            cod_nom=largo,
            nom_ape_p=largo,
            dni_p=largo,
            autorizacion=largo,
            validacion_detalle=largo,
        ),
        medico=_medico(NOMBRE=largo, CATEGORIA="CCC"),
    )
    for columna, ancho in anchos.items():
        valor = getattr(fila, columna)
        assert len(valor) <= ancho, f"O.S. {nro}: {columna} mide {len(valor)}, máximo {ancho}"


def test_las_obras_sociales_manuales_repiten_el_nombre_del_afiliado_en_paciente():
    """Boreal, Omint y Nobis lo hacen; Sancor, OSPJN y OSPM dejan relleno."""
    fila = _construir(_detalle(cod_obr="285", nom_ape_p="MAIDANA MARIA"))
    assert fila.PACIENTE == "MAIDANA MARIA"

    fila_sancor = _construir(_detalle(cod_obr="411", nom_ape_p="MAIDANA MARIA"))
    assert fila_sancor.PACIENTE == "a"
