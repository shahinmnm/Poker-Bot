from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import auth, health, game
from app.websocket_manager import websocket_endpoint

app = FastAPI(
    title="Poker Bot WebApp API",
    version="1.0.0"
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://poker.shahin8n.sbs"],  # Frontend domain
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router)
app.include_router(game.router, prefix="/game", tags=["game"])

# WebSocket endpoint
app.add_websocket_route("/ws", websocket_endpoint)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return await health.health_check()
