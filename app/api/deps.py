from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db

async def get_async_db(db: AsyncSession = Depends(get_db)) -> AsyncSession:
    return db