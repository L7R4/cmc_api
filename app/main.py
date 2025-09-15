from fastapi import FastAPI
from app.api.routes import api_router
from app.core.config import settings

app = FastAPI(
    title="CMC API",
    version="1.0",
    openapi_url="/openapi.json",
)
app.include_router(api_router, prefix="/api")

# por ejemplo, si quieres CORS:
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_LIST(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count", "Content-Range", "X-Offset", "X-Limit"],  # 👈
)