#!/usr/bin/env python3
"""FastAPI backend for Poker WebApp."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routes import auth, game

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Poker WebApp API starting...")
    
    # Log CORS configuration
    cors_origins = app.state.cors_origins
    logger.info(f"📍 CORS origins: {cors_origins}")
    
    # Log registered routes
    logger.info("📡 Routes registered:")
    for route in app.routes:
        if hasattr(route, "methods") and hasattr(route, "path"):
            logger.info(f"  {route.methods} {route.path}")
    
    yield
    
    logger.info("🛑 Poker WebApp API shutting down...")

# Initialize FastAPI app
app = FastAPI(
    title="Poker WebApp API",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Configuration
import os
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "https://poker.shahin8n.sbs").split(",")
app.state.cors_origins = CORS_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers with /api/ prefix (NEW STANDARD)
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(game.router, prefix="/api/game", tags=["Game"])

# Include routers WITHOUT prefix (LEGACY COMPATIBILITY)
app.include_router(auth.router, tags=["Authentication (Legacy)"])
app.include_router(game.router, prefix="/game", tags=["Game (Legacy)"])

# Health check endpoints
@app.get("/health")
@app.get("/api/health")
async def health_check():
    return JSONResponse(
        content={"status": "healthy", "service": "poker-webapp-api"},
        status_code=200
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
