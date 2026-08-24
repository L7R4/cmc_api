"""Los datos del propio Colegio Médico de Corrientes.

`institucion` es una sola fila (singleton). Los teléfonos y los mails salen a
tablas hijas porque son varios y cambian por separado.

Los mails van en su **propia** tabla y no junto a los teléfonos con un campo
`tipo`, aunque sería más corto: la columna `password_cifrada` haría que cualquier
consulta de teléfonos arrastrara la credencial. Con dos tablas, el secreto sólo
se toca cuando se consulta la tabla que lo tiene.

La contraseña va cifrada con Fernet, llave fuera de la base — ver
`app/core/secretos.py`.
"""
import datetime
from typing import List, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class Institucion(Base):
    """Fila única con los datos fiscales y de contacto del Colegio.

    Es un singleton: `app/modules/institucion/routes.py` la crea vacía la
    primera vez que alguien abre la pantalla y desde ahí siempre es un UPDATE.
    Se modela como tabla y no como constantes en el código porque son datos que
    cambian —el CBU cambió tres veces en cinco años— y cambiarlos no puede
    depender de un despliegue.

    Todos los campos son opcionales a propósito: la pantalla tiene que poder
    guardarse a medias mientras alguien junta la información.
    """

    __tablename__ = "institucion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    razon_social: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # Sin guiones ni puntos: se normaliza a 11 dígitos en el schema de entrada.
    cuit: Mapped[Optional[str]] = mapped_column(String(13), nullable=True)
    condicion_iva: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    ingresos_brutos: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    # 22 dígitos. Es contra lo que cobra el Colegio, así que va junto al alias y
    # al banco: un CBU suelto, sin saber de qué banco es, no sirve para nada.
    cbu: Mapped[Optional[str]] = mapped_column(String(22), nullable=True)
    alias_cbu: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    banco: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    titular_cuenta: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    domicilio: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    localidad: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    provincia: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    codigo_postal: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    sitio_web: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    horario_atencion: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    notas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    actualizado_en: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime, nullable=True, server_default=func.now(), onupdate=func.now()
    )
    # `ListadoMedico.ID` de quien guardó por última vez. Sin FK: el registro de
    # quién tocó los datos fiscales tiene que sobrevivir a la baja del legajo.
    actualizado_por: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    telefonos: Mapped[List["InstitucionTelefono"]] = relationship(
        "InstitucionTelefono",
        back_populates="institucion",
        cascade="all, delete-orphan",
        order_by="InstitucionTelefono.id",
    )
    emails: Mapped[List["InstitucionEmail"]] = relationship(
        "InstitucionEmail",
        back_populates="institucion",
        cascade="all, delete-orphan",
        order_by="InstitucionEmail.id",
    )


class InstitucionTelefono(Base):
    """Una línea del Colegio. Sin secretos: se puede listar sin cuidados."""

    __tablename__ = "institucion_telefonos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    institucion_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("institucion.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # "Conmutador", "Guardia", "Administración". Es lo que hace útil al número.
    etiqueta: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    numero: Mapped[str] = mapped_column(String(60), nullable=False)
    # No se valida el formato: conviven fijos con característica, celulares con
    # 15 y números que la gente anota con interno ("3794-123456 int. 4").
    notas: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    institucion: Mapped["Institucion"] = relationship("Institucion", back_populates="telefonos")


class InstitucionEmail(Base):
    """Una casilla de correo del Colegio, con su contraseña cifrada opcional.

    `password_cifrada` es un token Fernet (`app/core/secretos.py`), nunca texto
    plano, y no sale nunca en un listado: el `GET` devuelve `tiene_password` y el
    texto se entrega sólo por el endpoint dedicado, restringido a la lista de
    `INSTITUCION_CLAVES_SOCIOS` y auditado.
    """

    __tablename__ = "institucion_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    institucion_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("institucion.id", ondelete="CASCADE"), nullable=False, index=True
    )
    etiqueta: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    direccion: Mapped[str] = mapped_column(String(200), nullable=False)

    # Datos de configuración del cliente de correo. Están acá porque la
    # contraseña sin el servidor no alcanza para configurar nada, que es
    # justamente para lo que se guarda.
    servidor_entrante: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    servidor_saliente: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    password_cifrada: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    password_actualizada_en: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime, nullable=True
    )
    notas: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    institucion: Mapped["Institucion"] = relationship("Institucion", back_populates="emails")
