"""Puente **temporal** con el sistema viejo: espeja en `guardar_atencion` las
prestaciones que se cargan por el panel de validaciones nuevo.

## Por qué existe

El legacy todavía factura leyendo `guardar_atencion`. Hasta que se apague, una
prestación cargada por esta API tiene que aparecer también allá o se pierde en
la facturación del mes. Al revés no: lo que se carga en el sistema viejo sigue
su camino de siempre y no llega acá.

## Cómo está aislado

Todo el conocimiento del legacy vive en esta carpeta: el mapeo de columnas y las
rarezas de cada obra social. El sistema nuevo no se adaptó en nada para esto:

* `ValidadorOS` no ganó ningún campo del legacy. Las diferencias por obra social
  están en `perfiles.py`, un registro propio con la misma clave que `obras/`.
* No hubo migración. El vínculo entre las dos filas se anota en la columna JSON
  de traza que el módulo ya usaba (`validacion_respuesta`), con la clave
  `"legacy"` — el mismo lugar donde `ValidadorOS.anular()` deja `"anulacion"`.
* El sistema nuevo lo llama en dos líneas, las dos en `core/pipeline.py`, y le
  pasa sólo cosas que ya tenía en la mano (la fila grabada y el médico). No hay
  ningún dato que exista únicamente para alimentar al espejo.

**Para apagarlo:** borrar esta carpeta y las dos llamadas de `core/pipeline.py`
(están marcadas). No queda una sola columna, tabla ni campo huérfano; la clave
`"legacy"` de las trazas viejas queda como dato histórico inerte.

## Garantía

Falla abierto y corre siempre después del commit del sistema nuevo: si el espejo
no puede escribir, la prestación del médico igual queda cargada y el problema va
al log. El dueño del dato es `detalle_facturacion`.

Ver `espejo.py` para el detalle y `mapeo.py` para qué columnas importan.
"""
from app.modules.validaciones.legacy.espejo import replicar_alta, replicar_baja

__all__ = ["replicar_alta", "replicar_baja"]
