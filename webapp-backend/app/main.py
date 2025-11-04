from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import logging

from app.routes import auth, game

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Poker WebApp API")

# CORS
origins = os.getenv("CORS_ORIGINS", "https://poker.shahin8n.sbs").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth.router, prefix="/api")
app.include_router(game.router, prefix="/api")


@app.on_event("startup")
async def startup():
    logger.info("🚀 Poker WebApp API starting...")
    logger.info(f"📍 CORS origins: {origins}")


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "poker-webapp-api"}
