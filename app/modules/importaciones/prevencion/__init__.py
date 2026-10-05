"""Importación del reporte mensual de facturación de Prevención Salud (O.S. 103)."""

NRO_PREVENCION = 103

# Obras sociales en las que se puede cargar el reporte. La 888 es de prueba:
# se creó para ensayar importaciones sin tocar la facturación real de la 103.
OBRAS_SOCIALES = {
    NRO_PREVENCION: "Prevención Salud",
    888: "Prueba Prevención",
}
