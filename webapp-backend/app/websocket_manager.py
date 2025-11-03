from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict, Set
import json
import logging

logger = logging.getLogger(__name__)

# نگهداری اتصالات فعال
active_connections: Dict[str, Set[WebSocket]] = {}

async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time game updates."""
    await websocket.accept()
    
    game_id = None
    
    try:
        # دریافت game_id از client
        data = await websocket.receive_text()
        message = json.loads(data)
        game_id = message.get("game_id")
        
        if not game_id:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": "game_id required"
            }))
            await websocket.close()
            return
        
        # اضافه کردن به لیست connections
        if game_id not in active_connections:
            active_connections[game_id] = set()
        active_connections[game_id].add(websocket)
        
        logger.info(f"WebSocket connected: game_id={game_id}")
        
        # ارسال پیام خوش‌آمدگویی
        await websocket.send_text(json.dumps({
            "type": "connected",
            "game_id": game_id
        }))
        
        # نگه داشتن connection
        while True:
            data = await websocket.receive_text()
            # Handle incoming messages
            logger.debug(f"Received: {data}")
            
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: game_id={game_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # حذف از لیست connections
        if game_id and game_id in active_connections:
            active_connections[game_id].discard(websocket)
            if not active_connections[game_id]:
                del active_connections[game_id]

async def broadcast_to_game(game_id: str, message: dict):
    """Broadcast message to all clients watching a game."""
    if game_id not in active_connections:
        return
    
    message_str = json.dumps(message)
    
    # ارسال به تمام clients
    dead_connections = set()
    for connection in active_connections[game_id]:
        try:
            await connection.send_text(message_str)
        except Exception:
            dead_connections.add(connection)
    
    # حذف connections مرده
    for connection in dead_connections:
        active_connections[game_id].discard(connection)
