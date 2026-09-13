"""Normas operativas del boletín de consulta común.

Las normas operativas son noticias del portal (las que el editor etiqueta con el
badge "Normas Operativas") asociadas a una o más obras sociales. Esta ruta es la
que consume la pantalla del boletín para poner el link en la fila de cada obra
social, y devuelve el listado completo de una: la tabla tiene ~300 filas y una
request por fila sería el peor uso posible del endpoint.

A propósito **no** devuelve `contenido`: el boletín sólo muestra título y fecha,
y el cuerpo de la noticia ya lo sirve `/api/noticias/{id}` cuando el usuario
hace click.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models.contenido import Noticia, NoticiaObraSocial
from app.modules.catalogs.schemas import NormaOperativaOut

router = APIRouter()


@router.get(
    "/normas",
    response_model=dict,
    summary="Listar normas operativas asociadas a obras sociales",
)
async def listar_normas(db: AsyncSession = Depends(get_db)):
    """Noticias publicadas con al menos una obra social asociada.

    Los borradores quedan afuera sin importar el scope de quien consulta: el
    boletín lo exporta a PDF y a Excel, así que una norma sin publicar que se
    colara acá terminaría en un archivo que circula fuera del panel.
    """
    stmt = (
        select(
            Noticia.id,
            Noticia.titulo,
            Noticia.badge,
            Noticia.fecha_creacion,
            NoticiaObraSocial.nro_obrasocial,
        )
        .join(NoticiaObraSocial, NoticiaObraSocial.noticia_id == Noticia.id)
        .where(Noticia.publicada.is_(True))
        .order_by(desc(Noticia.fecha_creacion))
    )

    # Una sola query; el agrupado por noticia se arma acá para no pagar N+1 ni
    # devolver el título repetido por cada obra social.
    normas: dict[int, NormaOperativaOut] = {}
    for id_, titulo, badge, fecha, nro in (await db.execute(stmt)).all():
        norma = normas.get(id_)
        if norma is None:
            normas[id_] = NormaOperativaOut(
                id=id_,
                titulo=titulo,
                badge=badge,
                fecha=fecha,
                obras_sociales=[nro],
            )
        else:
            norma.obras_sociales.append(nro)

    return {"items": list(normas.values())}
