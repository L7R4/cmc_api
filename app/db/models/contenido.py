import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, BigInteger, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class Noticia(Base):
    __tablename__ = "noticias"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    titulo           = Column(String(255), nullable=False)
    contenido        = Column(Text, nullable=False)
    resumen          = Column(String(1000), nullable=False)
    autor            = Column(String(120), nullable=False, default="Colegio Médico de Corrientes")
    publicada        = Column(Boolean, nullable=False, server_default="1")
    tipo: Mapped[str] = mapped_column(String(10, 'utf8_spanish2_ci'), nullable=False, server_default=text("'Noticia'"))
    portada          = Column(String(500), nullable=True)
    badge            = Column(String(80), nullable=True)

    fecha_creacion   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    fecha_actualizacion = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_noticias_publicada_fecha", "publicada", "fecha_creacion"),
    )

    documentos = relationship(
        "DocumentoNoticias",
        back_populates="noticia",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class DocumentoNoticias(Base):
    __tablename__ = "documentos_noticias"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    noticia_id    = Column(Integer, ForeignKey("noticias.id", ondelete="CASCADE"), index=True, nullable=False)

    label         = Column(String(50),  nullable=True)
    original_name = Column(String(255), nullable=False)
    filename      = Column(String(255), nullable=False)
    content_type  = Column(String(100), nullable=True)
    size          = Column(Integer, nullable=True)
    path          = Column(String(512), nullable=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
     DateTime(timezone=True),
     server_default=func.now(),
     nullable=False,
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    noticia       = relationship("Noticia", back_populates="documentos")


class PublicidadMedico(Base):
    __tablename__ = "publicidad_medicos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    medico_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    adjunto_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    adjunto_content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    adjunto_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    adjunto_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
