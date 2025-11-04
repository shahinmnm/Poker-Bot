#!/usr/bin/env python3
"""
Main FastAPI application for Poker WebApp backend.
"""

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

from app.routes import auth, game

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Poker Bot WebApp API",
    version="1.0.0",
    description="Backend API for Telegram Poker Bot WebApp"
)

# CORS Configuration
cors_origins = os.getenv("CORS_ORIGINS", "https://poker.shahin8n.sbs").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router)
app.include_router(game.router)

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "poker-webapp-api"
    }

@app.on_event("startup")
async def startup_event():
    logger.info("🚀 Poker WebApp API starting...")
    logger.info(f"📍 CORS origins: {cors_origins}")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("🛑 Poker WebApp API shutting down...")
