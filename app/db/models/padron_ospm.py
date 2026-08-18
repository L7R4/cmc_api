"""
Padrón de afiliados de OSPM (O.S. 433), para el validador del panel.

OSPM no expone ningún servicio: la validación es contra este padrón, que el
Colegio importa periódicamente desde el CSV que manda la obra social. Sin
padrón cargado no valida nadie.

Se usa **`clientes_ospm`, la tabla del sistema legacy** (la que carga
`importar_padron_ospm.php`), no una tabla nueva: el padrón es uno solo y tenerlo
duplicado significaba que el PHP viejo y la API pudieran validar contra padrones
distintos. Por eso las columnas están en UPPER_CASE y con los tipos originales
—`DU` varchar(8), `AFILIADO` varchar(30)—, que el importador respeta.
"""
from sqlalchemy import Index, String
from sqlalchemy.dialects.mysql import INTEGER
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# `ACTIVO` es un varchar(1) del legacy: sólo un afiliado en 'S' valida.
OSPM_ACTIVO = "S"
OSPM_INACTIVO = "N"


class ClientesOspm(Base):
    __tablename__ = "clientes_ospm"

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True, autoincrement=True)
    # DNI del afiliado — la clave con la que busca el validador.
    DU: Mapped[str] = mapped_column(String(8), nullable=False)
    CUIT: Mapped[str] = mapped_column(String(11), nullable=False)
    # Nombre y apellido, tal como lo manda la obra social.
    AFILIADO: Mapped[str] = mapped_column(String(30), nullable=False)
    ACTIVO: Mapped[str] = mapped_column(String(1), nullable=False)

    __table_args__ = (
        # El validador busca por documento; el índice de AFILIADO ya viene del legacy.
        Index("ix_clientes_ospm_du", "DU"),
    )

    # ── Accesores con el nombre del dominio ──────────────────────────────────
    # El resto del módulo habla de documento/nombre/activo; estas propiedades
    # evitan que las mayúsculas del legacy se filtren a la lógica.
    @property
    def documento(self) -> str:
        return self.DU

    @property
    def nombre(self) -> str:
        return self.AFILIADO

    @property
    def activo(self) -> bool:
        return (self.ACTIVO or "").strip().upper() == OSPM_ACTIVO
