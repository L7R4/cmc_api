"""Tabla explícita `_REPARTO_POR_FUNCION` de `liquidacion/service.py`.

Reemplaza a una cadena if/elif cuyo `else` se comportaba igual que 'H' — un
tpo_funcion desconocido se liquidaba en silencio como si fuera el médico
principal. Ver la auditoría 2026-09-17 que encontró 27 filas 'A2' así
liquidadas en $0 (normalizadas a 'A' antes de este cambio, ver docs/api/).

Pruebas puras: no tocan la base, ejercitan directamente la tabla + el branch
de `observados` reproduciendo el fragmento equivalente de
`build_detalles_from_cmc`.
"""
from decimal import Decimal

from app.modules.liquidacion.service import _REPARTO_POR_FUNCION, to_dec


class _FilaFalsa:
    """Doble de `DetalleFacturacionCMC`: sólo los campos que lee el reparto."""

    def __init__(self, tpo_funcion, honorarios=0, gastos=0, ayudante=0, id_detalle_prestaciones=1, cod_med="1"):
        self.tpo_funcion = tpo_funcion
        self.honorarios = honorarios
        self.gastos = gastos
        self.ayudante = ayudante
        self.id_detalle_prestaciones = id_detalle_prestaciones
        self.cod_med = cod_med


def _reparto(df) -> tuple[Decimal, Decimal, bool]:
    """Mismo fragmento que `build_detalles_from_cmc` — devuelve
    (honorarios, gastos, hubo_observado)."""
    observados: list[dict] = []
    funcion = (df.tpo_funcion or "H").upper()
    reparto = _REPARTO_POR_FUNCION.get(funcion)
    if reparto is None:
        observados.append({
            "cmc_detalle_id": df.id_detalle_prestaciones,
            "cod_med": df.cod_med,
            "razon": "tpo_funcion_desconocido",
            "tpo_funcion": df.tpo_funcion,
        })
        reparto = _REPARTO_POR_FUNCION["H"]
    col_hon, col_gas = reparto
    honorarios = to_dec(getattr(df, col_hon)) if col_hon else Decimal("0")
    gastos = to_dec(getattr(df, col_gas)) if col_gas else Decimal("0")
    return honorarios, gastos, bool(observados)


def test_h_toma_solo_honorarios():
    h, g, obs = _reparto(_FilaFalsa("H", honorarios=100, gastos=999, ayudante=999))
    assert (h, g, obs) == (Decimal("100.00"), Decimal("0.00"), False)


def test_hg_toma_honorarios_y_gastos():
    h, g, obs = _reparto(_FilaFalsa("HG", honorarios=100, gastos=50, ayudante=999))
    assert (h, g, obs) == (Decimal("100.00"), Decimal("50.00"), False)


def test_g_toma_solo_gastos():
    h, g, obs = _reparto(_FilaFalsa("G", honorarios=999, gastos=50, ayudante=999))
    assert (h, g, obs) == (Decimal("0.00"), Decimal("50.00"), False)


def test_a_toma_ayudante_como_honorarios():
    h, g, obs = _reparto(_FilaFalsa("A", honorarios=999, gastos=999, ayudante=30))
    assert (h, g, obs) == (Decimal("30.00"), Decimal("0.00"), False)


def test_p_pediatra_toma_honorarios_de_su_propio_codigo():
    h, g, obs = _reparto(_FilaFalsa("P", honorarios=45, gastos=999, ayudante=999))
    assert (h, g, obs) == (Decimal("45.00"), Decimal("0.00"), False)


def test_funcion_desconocida_se_observa_y_cae_a_h():
    h, g, obs = _reparto(_FilaFalsa("A2", honorarios=0, gastos=0, ayudante=30))
    # Antes de la normalización de datos, 'A2' caía acá: se liquidaba en $0
    # (toma honorarios, que en esas filas era 0) SIN que quedara ningún rastro.
    assert (h, g) == (Decimal("0.00"), Decimal("0.00"))
    assert obs is True  # ahora queda registrado en `observados`, ya no es mudo


def test_tpo_funcion_null_se_trata_como_h():
    h, g, obs = _reparto(_FilaFalsa(None, honorarios=100, gastos=999, ayudante=999))
    assert (h, g, obs) == (Decimal("100.00"), Decimal("0.00"), False)
