from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.api.routes import api_router
from app.core.config import settings
from app.auth.router import router as auth_router
from fastapi.middleware.cors import CORSMiddleware

import os
os.environ.setdefault("PASSLIB_BCRYPT_MINIMAL", "1")

app = FastAPI(
    title="CMC API",
    version="1.0",
    openapi_url="/openapi.json",
)

app.include_router(api_router, prefix="/api")
app.include_router(auth_router)  # expone /auth/*

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_LIST(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
    expose_headers=["X-Total-Count", "Content-Range", "X-Offset", "X-Limit"],
)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")