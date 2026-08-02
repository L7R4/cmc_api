"""
Modelos del sistema de nomenclador, valores y precios del CMC.

Todas las tablas usan prefijo nm_ para evitar conflicto con la tabla
legacy 'nomenclador' (app/db/models/legacy.py).
"""
import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    JSON, DECIMAL, Boolean, Computed, Date, DateTime, Enum, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.common.money import quantize_money
from app.db.base import Base


class NomencladorCMC(Base):
    """Catálogo operativo del Colegio. Un código por práctica; la vía/técnica
    (tradicional/laparoscópica) es un atributo de la prestación, no del código
    (ver detalle_facturacion.via y app/modules/nomenclador/service_vias.py)."""
    __tablename__ = "nm_nomenclador"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    codigo: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    # Código del Nomenclador Nacional al que corresponde este código del Colegio.
    # NULL = no identificado como nacional (código propio del Colegio o sin match).
    # Se cargó por comparación contra el extracto del PDF (ver scripts/compare_nomenclador.py).
    codigo_nacional: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    descripcion: Mapped[str] = mapped_column(String(255), nullable=False)
    categoria: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    complejidad: Mapped[Optional[str]] = mapped_column(
        Enum("baja", "media", "alta", name="nm_complejidad_enum"), nullable=True
    )
    # True → exime de la validación nomenclador_especialidad
    sin_restriccion_especialidad: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # Unidades por defecto al crear ValorComponente calculable sin cantidad explícita
    unidades_honorarios: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(10, 2), nullable=True)
    unidades_ayudante: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(10, 2), nullable=True)
    unidades_gastos: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(10, 2), nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    observacion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    especialidades: Mapped[List["NomencladorEspecialidad"]] = relationship(
        back_populates="nomenclador", cascade="all, delete-orphan", lazy="selectin"
    )
    habilitaciones_medico: Mapped[List["MedicoCodigoHabilitado"]] = relationship(
        back_populates="nomenclador", cascade="all, delete-orphan"
    )
    valores: Mapped[List["Valor"]] = relationship(back_populates="nomenclador")

    __table_args__ = (
        Index("ix_nm_nomenclador_codigo", "codigo"),
        Index("ix_nm_nomenclador_complejidad", "complejidad"),
    )


class NomencladorEspecialidad(Base):
    """Habilitación general de un código para una especialidad."""
    __tablename__ = "nm_nomenclador_especialidad"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nomenclador_id: Mapped[int] = mapped_column(
        ForeignKey("nm_nomenclador.id"), nullable=False
    )
    # FK lógica — no FK real porque ID_COLEGIO_ESPE no es PK
    especialidad_id_colegio: Mapped[int] = mapped_column(Integer, nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    observacion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    nomenclador: Mapped["NomencladorCMC"] = relationship(back_populates="especialidades")

    __table_args__ = (
        UniqueConstraint(
            "nomenclador_id", "especialidad_id_colegio", name="uq_nm_nom_esp"
        ),
        Index("ix_nm_nom_esp_especialidad", "especialidad_id_colegio"),
    )


class MedicoCodigoHabilitado(Base):
    """Excepciones individuales por médico sobre habilitación de códigos."""
    __tablename__ = "nm_medico_codigo_habilitado"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    medico_id: Mapped[int] = mapped_column(
        ForeignKey("listado_medico.ID"), nullable=False
    )
    nomenclador_id: Mapped[int] = mapped_column(
        ForeignKey("nm_nomenclador.id"), nullable=False
    )
    tipo: Mapped[str] = mapped_column(
        Enum("habilita", "inhabilita", name="nm_tipo_habilitacion_enum"), nullable=False
    )
    vigencia_desde: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    vigencia_hasta: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    motivo: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    observacion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    nomenclador: Mapped["NomencladorCMC"] = relationship(back_populates="habilitaciones_medico")

    __table_args__ = (
        Index("ix_nm_mch_medico_nom", "medico_id", "nomenclador_id"),
        Index("ix_nm_mch_tipo", "tipo"),
        Index("ix_nm_mch_vigencia", "vigencia_desde", "vigencia_hasta"),
    )


class Homologador(Base):
    """Mapeo codigo_origen (de la OS) → NomencladorCMC."""
    __tablename__ = "nm_homologador"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # FK lógica a obras_sociales.NRO_OBRASOCIAL (entero, no es PK real en esa tabla)
    obra_social_nro: Mapped[int] = mapped_column(Integer, nullable=False)
    codigo_origen: Mapped[str] = mapped_column(String(50), nullable=False)
    nomenclador_id: Mapped[int] = mapped_column(
        ForeignKey("nm_nomenclador.id"), nullable=False
    )
    descripcion_origen: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    observacion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    nomenclador: Mapped["NomencladorCMC"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "obra_social_nro", "codigo_origen", name="uq_nm_homologador_os_origen"
        ),
        Index("ix_nm_homologador_os_origen", "obra_social_nro", "codigo_origen"),
        Index("ix_nm_homologador_nomenclador", "nomenclador_id"),
    )


class Galeno(Base):
    """
    Precio unitario histórico de galenos/gastos/módulos pactado con cada OS.
    Identidad natural: (obra_social_nro, codigo, nivel, vigencia_desde).
    nivel NULL → el galeno no está nivelado (un único precio unitario).
    nivel N    → una fila por nivel; cada nivel puede tener su propio valor_unitario.
    Además funciona como plantilla por OS: unidades_{honorarios,ayudante,gastos} son
    los defaults por concepto que toma el ValorComponente al asociarse (ver pre-fill).
    """
    __tablename__ = "nm_galenos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    obra_social_nro: Mapped[int] = mapped_column(Integer, nullable=False)
    # Clave-identidad slug derivada del nombre: 'galeno_quirurgico', 'gasto_quirurgico', etc.
    codigo: Mapped[str] = mapped_column(String(100), nullable=False)
    nombre: Mapped[str] = mapped_column(String(200), nullable=False)
    # Nivel del galeno (ej: cirugía adulto niveles 1-7). NULL = sin niveles.
    nivel: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Columna generada para poder declarar la unique con nivel NULL (-1 = sin nivel)
    nivel_key: Mapped[int] = mapped_column(
        Integer, Computed("coalesce(nivel, -1)", persisted=True), nullable=False
    )
    vigencia_desde: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    vigencia_hasta: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    valor_unitario: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
    # Unidades-plantilla por concepto (por OS): defaults que toma el ValorComponente
    # calculable al asociar este galeno a un código con cantidad=0. Nullable: si el
    # concepto no tiene unidad acá, el pre-fill cae a nm_nomenclador.unidades_* (NN).
    unidades_honorarios: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(10, 2), nullable=True)
    unidades_ayudante: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(10, 2), nullable=True)
    unidades_gastos: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(10, 2), nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    observacion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    componentes: Mapped[List["ValorComponente"]] = relationship(back_populates="galeno")

    __table_args__ = (
        UniqueConstraint(
            "obra_social_nro", "codigo", "nivel_key", "vigencia_desde",
            name="uq_nm_galenos_os_codigo_nivel_vig",
        ),
        Index("ix_nm_galenos_os_codigo_nivel", "obra_social_nro", "codigo", "nivel"),
        Index("ix_nm_galenos_vigencia", "vigencia_desde", "vigencia_hasta"),
    )


class GalenoPlantilla(Base):
    """
    Plantillas prearmadas de galenos, cargadas a mano por el programador (sin acceso
    de usuarios ni endpoints de escritura). Un `grupo` agrupa las filas que componen
    un galeno completo listo para instanciar: una fila por nivel (o una sola con
    nivel NULL si no es nivelado).

    El front las consulta (GET) para pre-armar el formulario de
    POST /galenos/crear_niveles: `nombre` + `niveles[]` calzan casi 1:1 con
    GalenoCrearNivelesIn. `valor_unitario` se carga en 0 — es informativo, el precio
    real se pacta por OS al instanciar.

    Sin obra_social_nro (genéricas, reutilizables para cualquier OS) y sin
    vigencia/activo/observacion (no aplican a una plantilla).

    `codigo` es el slug real que tendrá el galeno en nm_galenos al instanciarse
    (debe ser igual a slugify_codigo(nombre), ver GalenoCreate/GalenoCrearNivelesIn).
    `grupo` identifica el conjunto completo, ej. 'cirugia_adulto_de_7_niveles'.
    """
    __tablename__ = "nm_galenos_plantilla"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    grupo: Mapped[str] = mapped_column(String(100), nullable=False)
    codigo: Mapped[str] = mapped_column(String(100), nullable=False)
    nombre: Mapped[str] = mapped_column(String(200), nullable=False)
    nivel: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Columna generada para poder declarar la unique con nivel NULL (-1 = sin nivel)
    nivel_key: Mapped[int] = mapped_column(
        Integer, Computed("coalesce(nivel, -1)", persisted=True), nullable=False
    )
    valor_unitario: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
    unidades_honorarios: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(10, 2), nullable=True)
    unidades_ayudante: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(10, 2), nullable=True)
    unidades_gastos: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(10, 2), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "grupo", "nivel_key", name="uq_nm_galenos_plantilla_grupo_nivel"
        ),
        Index("ix_nm_galenos_plantilla_grupo", "grupo"),
    )


class Valor(Base):
    """
    Variante de precio para un código+OS+vigencia.
    La identidad de la variante es (origen, especialidad_id_colegio):
      origen → categoría/procedencia de la regla de precio; fija la PRIORIDAD del
               lookup (NE > NNE > NN). La prioridad NO vive en DB: es la posición en
               ORIGEN_PRIORIDAD (service.py). El String permite sumar orígenes sin migrar.
      especialidad_id_colegio → NULL = sin perfil · N = exige esa especialidad.
               Solo lo usa NE (NNE/NN siempre van NULL).
    El lookup elige por mayor prioridad de origen y, dentro del origen, match de
    especialidad (orden de slots del médico) > sin especialidad.
    Máximo un activo por (obra_social_nro, nomenclador_id, origen, especialidad_id_colegio) — app-level.
    """
    __tablename__ = "nm_valores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    obra_social_nro: Mapped[int] = mapped_column(Integer, nullable=False)
    nomenclador_id: Mapped[int] = mapped_column(
        ForeignKey("nm_nomenclador.id"), nullable=False
    )
    # Categoría/procedencia del valor: 'NE' | 'NNE' | 'NN' (validado en código contra
    # schemas.Origen / service.ORIGEN_PRIORIDAD). NO es ENUM de DB para poder sumar
    # orígenes sin migración. La prioridad de lookup se deriva en código.
    origen: Mapped[str] = mapped_column(String(10), nullable=False)
    # Snapshot denormalizado del codigo para consultas rápidas
    codigo: Mapped[str] = mapped_column(String(20), nullable=False)
    descripcion: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Nivel numérico que asigna esta OS al código (independiente de complejidad del nomenclador)
    nivel: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Override de complejidad por OS; NULL → hereda NomencladorCMC.complejidad
    complejidad: Mapped[Optional[str]] = mapped_column(
        Enum("baja", "media", "alta", name="nm_valor_complejidad_enum"), nullable=True
    )
    # FK lógica a especialidad.ID_COLEGIO_ESPE — variante de precio por especialidad
    especialidad_id_colegio: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    # Máximo de ayudantes admitidos para este código+OS. NULL = no lleva ayudantes (0).
    cantidad_ayudantes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # True → el código se factura "por presupuesto": no hay precio pactado en el
    # sistema, la OS informa el importe por fuera. Los componentes H/G/A quedan en 0
    # y el operador carga el monto a mano al facturar (modo manual).
    por_presupuesto: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    vigencia_desde: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    vigencia_hasta: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    estado: Mapped[str] = mapped_column(
        Enum("activo", "cerrado", name="nm_estado_valor_enum"),
        nullable=False,
        default="activo",
        server_default="activo",
    )
    observacion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    nomenclador: Mapped["NomencladorCMC"] = relationship(back_populates="valores")
    componentes: Mapped[List["ValorComponente"]] = relationship(
        back_populates="valor", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def modalidad(self) -> str:
        """Modalidad de la ecuación: 'por_presupuesto' | 'galeno' | 'fijo'.

        Homogénea por validación: todos los componentes son calculables o todos fijos.
        """
        if self.por_presupuesto:
            return "por_presupuesto"
        if any(c.galeno_id is not None for c in self.componentes):
            return "galeno"
        return "fijo"

    __table_args__ = (
        Index("ix_nm_valores_os_nom", "obra_social_nro", "nomenclador_id"),
        Index("ix_nm_valores_especialidad", "especialidad_id_colegio"),
        Index("ix_nm_valores_origen", "origen"),
        Index("ix_nm_valores_codigo", "codigo"),
        Index("ix_nm_valores_vigencia", "vigencia_desde", "vigencia_hasta"),
        Index("ix_nm_valores_nivel", "nivel"),
        Index("ix_nm_valores_complejidad", "complejidad"),
        Index("ix_nm_valores_estado", "estado"),
        Index("ix_nm_valores_por_presupuesto", "por_presupuesto"),
    )


class ValorComponente(Base):
    """Fórmula de cálculo de un Valor: componentes fijos y calculables por galeno."""
    __tablename__ = "nm_valor_componentes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    valor_id: Mapped[int] = mapped_column(ForeignKey("nm_valores.id"), nullable=False)
    concepto: Mapped[str] = mapped_column(
        Enum("Honorarios", "Ayudante", "Gastos", name="nm_concepto_componente_enum"),
        nullable=False,
    )
    # Non-null si cantidad > 0 (calculable). Null si precio fijo (cantidad = 0).
    galeno_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("nm_galenos.id"), nullable=True
    )
    # 0 = precio fijo; > 0 = unidades del galeno a multiplicar
    cantidad: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 4), nullable=False, default=Decimal("0"), server_default="0"
    )
    # Solo se usa cuando galeno_id IS NULL (precio fijo embebido)
    valor_unitario: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(14, 2), nullable=True)
    orden: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    observacion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    valor: Mapped["Valor"] = relationship(back_populates="componentes")
    galeno: Mapped[Optional["Galeno"]] = relationship(back_populates="componentes", lazy="joined")

    # Campos derivados para la API (galeno viene lazy="joined": sin queries extra).
    @property
    def tipo(self) -> str:
        """'calculable' (precio del galeno × cantidad) | 'fijo' (precio embebido)."""
        return "calculable" if self.galeno_id is not None else "fijo"

    @property
    def galeno_codigo(self) -> Optional[str]:
        return self.galeno.codigo if self.galeno else None

    @property
    def galeno_nivel(self) -> Optional[int]:
        return self.galeno.nivel if self.galeno else None

    @property
    def precio_unitario(self) -> Optional[Decimal]:
        """VU efectivo a mostrar: del galeno si es calculable, del componente si es fijo."""
        if self.galeno_id is not None:
            return self.galeno.valor_unitario if self.galeno else None
        return self.valor_unitario

    @property
    def subtotal(self) -> Decimal:
        """Aporte del componente al precio: cantidad × VU (calculable) o VU fijo.
        Redondeado a 2 decimales — cantidad es DECIMAL(10,4), sin quantize el producto
        arrastra hasta 6 decimales."""
        if self.galeno_id is not None:
            vu = self.galeno.valor_unitario if self.galeno else Decimal("0")
            return quantize_money(self.cantidad * vu)
        return self.valor_unitario or Decimal("0")

    __table_args__ = (
        Index("ix_nm_vc_valor_id", "valor_id"),
        Index("ix_nm_vc_galeno_id", "galeno_id"),
    )


class HistorialPrecioCodigo(Base):
    """
    Tabla operativa materializada. Se consulta con lookup O(1) al cargar prestaciones.
    Se escribe SOLO por el motor de service.py; nunca manualmente.
    precio_total = suma de los 3 componentes del Valor (no hay opcionales).
    """
    __tablename__ = "nm_historial_precio_codigo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nomenclador_id: Mapped[int] = mapped_column(
        ForeignKey("nm_nomenclador.id"), nullable=False
    )
    obra_social_nro: Mapped[int] = mapped_column(Integer, nullable=False)
    # Categoría/procedencia del valor (NE|NNE|NN) — parte de la identidad de la variante
    origen: Mapped[str] = mapped_column(String(10), nullable=False)
    # Variante del valor al que pertenece esta fila (NULL = sin especialidad)
    especialidad_id_colegio: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Columna generada para la unique con NULL (-1 = variante base)
    especialidad_key: Mapped[int] = mapped_column(
        Integer, Computed("coalesce(especialidad_id_colegio, -1)", persisted=True),
        nullable=False,
    )
    vigencia_desde: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    vigencia_hasta: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    precio_total: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
    valores_id: Mapped[int] = mapped_column(ForeignKey("nm_valores.id"), nullable=False)
    # JSON con desglose: [{concepto, tipo, galeno_codigo, cantidad, valor_unitario, subtotal}]
    componentes_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    motivo_cambio: Mapped[str] = mapped_column(
        Enum(
            "carga_inicial",
            "galeno_actualizado",
            "valor_fijo_actualizado",
            "valores_estructura",
            "replicacion",
            "reversion",
            "migracion_legacy",
            name="nm_motivo_cambio_enum",
        ),
        nullable=False,
    )
    # ID del galeno / valores que disparó este cambio
    referencia_cambio_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Cuándo se registró el cambio en el sistema (≠ vigencia_desde)
    fecha_cambio: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    nomenclador: Mapped["NomencladorCMC"] = relationship()
    valores: Mapped["Valor"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "nomenclador_id", "obra_social_nro", "origen", "especialidad_key", "vigencia_desde",
            name="uq_nm_historial_precio",
        ),
        Index(
            "ix_nm_historial_nom_os_vigencia",
            "nomenclador_id", "obra_social_nro", "vigencia_desde", "vigencia_hasta",
        ),
        Index("ix_nm_historial_os_vigencia", "obra_social_nro", "vigencia_desde"),
        Index("ix_nm_historial_motivo", "motivo_cambio"),
        Index("ix_nm_historial_fecha_cambio", "fecha_cambio"),
    )
