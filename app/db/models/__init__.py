from app.db.base import Base, AuditMixin

from app.db.models.legacy import (
    Avisos, Clinicas, CodigoDescripcion, CodigoNomenclador,
    Codigoprestacionswiss, Consulta, EspeCod, EspeCodSwiss,
    GuardarIoscor, GuardarRefacturacion, MedicoObraSocial,
    Nomenclador, NomencladorIoscor, Paciente,
    UnidadNomenclador, UnidadNomenclador10, UnidadNomenclador7,
    UnidadNomencladorInf, UsuarioColegio, ValidarUsuario,
    ValorFijo, ValorNomencladoFijo, ValorNomencladoSwiss,
    ValorNomencladorNacional, ValorPrestacion10,
    ValorPrestacion7, ValorPrestacionInf,
)

from app.db.models.medico import ListadoMedico, Documento
from app.db.models.liquidacion import (
    GuardarAtencion, Pago, Liquidacion,
    DetalleLiquidacion, PagoMedico, Recibo,
)
from app.db.models.financiero import (
    LoteAjuste, Ajuste, Descuentos, SocioDescuento,
    Deduccion, DeduccionAplicacion,
)
from app.db.models.catalogs import (
    Especialidad, ObrasSociales, Periodos, ValorPrestacion,
    PeriodosDoctor, ValoresBoletin, ValoresBoletinHistorial, ValoresObrasocial,
    BoletinObservacion, BoletinObservacionPlantilla,
    ObraSocialContacto, ObraSocialDireccion, ObraSocialDocumento,
    ValoresEticos,
)
from app.db.models.rbac import Role, Permission, UserRole, RolePermission, UserPermission
from app.db.models.auditoria import AuditLog
from app.db.models.contenido import Noticia, DocumentoNoticias, PublicidadMedico
from app.db.models.solicitud import SolicitudRegistro
from app.db.models.cmc_facturacion import (
    DetalleFacturacionCMC, FacturacionCMC, Afiliado, PeriodoMedicoActual,
)
from app.db.models.nomenclador_cmc import (
    NomencladorCMC, NomencladorEspecialidad, MedicoCodigoHabilitado,
    Homologador, Galeno, GalenoPlantilla, Valor, ValorComponente,
    HistorialPrecioCodigo,
)

__all__ = [
    "Base", "AuditMixin",
    # legacy
    "Avisos", "Clinicas", "CodigoDescripcion", "CodigoNomenclador",
    "Codigoprestacionswiss", "Consulta", "EspeCod", "EspeCodSwiss",
    "GuardarIoscor", "GuardarRefacturacion", "MedicoObraSocial",
    "Nomenclador", "NomencladorIoscor", "Paciente",
    "UnidadNomenclador", "UnidadNomenclador10", "UnidadNomenclador7",
    "UnidadNomencladorInf", "UsuarioColegio", "ValidarUsuario",
    "ValorFijo", "ValorNomencladoFijo", "ValorNomencladoSwiss",
    "ValorNomencladorNacional", "ValorPrestacion10",
    "ValorPrestacion7", "ValorPrestacionInf",
    # medico
    "ListadoMedico", "Documento",
    # liquidacion
    "GuardarAtencion", "Pago", "Liquidacion",
    "DetalleLiquidacion", "PagoMedico", "Recibo",
    # financiero
    "LoteAjuste", "Ajuste", "Descuentos", "SocioDescuento",
    "Deduccion", "DeduccionAplicacion",
    # catalogs
    "Especialidad", "ObrasSociales", "Periodos", "PeriodosDoctor",
    "ValoresBoletin", "ValoresBoletinHistorial", "ValoresObrasocial", "ValorPrestacion",
    "BoletinObservacion", "BoletinObservacionPlantilla",
    "ObraSocialContacto", "ObraSocialDireccion", "ObraSocialDocumento",
    "ValoresEticos",
    # rbac
    "Role", "Permission", "UserRole", "RolePermission", "UserPermission",
    # auditoria
    "AuditLog",
    # contenido
    "Noticia", "DocumentoNoticias", "PublicidadMedico",
    # solicitud
    "SolicitudRegistro",
    # cmc_facturacion (detalle/facturacion CMC + padrón afiliado + período médico)
    "DetalleFacturacionCMC", "FacturacionCMC", "Afiliado", "PeriodoMedicoActual",
    # nomenclador / valores (sistema nuevo)
    "NomencladorCMC", "NomencladorEspecialidad", "MedicoCodigoHabilitado",
    "Homologador", "Galeno", "GalenoPlantilla", "Valor", "ValorComponente",
    "HistorialPrecioCodigo",
]
