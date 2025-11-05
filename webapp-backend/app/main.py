# webapp-backend/app/main.py
from __future__ import annotations

import logging
import os
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("app.main")
logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))

# -----------------------------------------------------------------------------
# App & CORS
# -----------------------------------------------------------------------------

def _split_csv(env_val: str | None) -> List[str]:
    if not env_val:
        return []
    return [x.strip() for x in env_val.split(",") if x.strip()]

CORS_ENV = os.environ.get("CORS_ORIGINS") or os.environ.get("POKER_CORS_ORIGINS") or "*"
ALLOWED = _split_csv(CORS_ENV) or ["*"]

app = FastAPI(title="Poker WebApp API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger.info("🚀 Poker WebApp API starting...")
logger.info("📍 CORS origins: %s", ALLOWED)

# -----------------------------------------------------------------------------
# Include EXISTING routers if your project already has them
# (We use try/except so this file is safe regardless of your code layout.)
# -----------------------------------------------------------------------------

def _include_optional_router(import_path: str, *, prefix: str | None = None) -> bool:
    """
    Try to import a router at e.g. 'app.routers.auth:router' and include it.
    Returns True if included, False if not present.
    """
    try:
        module_path, attr = import_path.split(":")
        mod = __import__(module_path, fromlist=[attr])
        router = getattr(mod, attr)
        if prefix:
            app.include_router(router, prefix=prefix)
        else:
            app.include_router(router)
        logger.info("✅ Included router %s (prefix=%s)", import_path, prefix or "")
        return True
    except Exception as e:
        logger.info("↩️  Skipping optional router %s (%s)", import_path, e)
        return False

# Common project layouts we’ve seen in your logs
_include_optional_router("app.routers.auth:router")                     # /auth/*
_include_optional_router("app.routers.auth:router", prefix="/api")      # /api/auth/*

_include_optional_router("app.routers.game:router")                     # /game/*
_include_optional_router("app.routers.game:router", prefix="/api")      # /api/game/*

# If you have a dedicated health router, we’ll include that too
_include_optional_router("app.routers.health:router")
_include_optional_router("app.routers.health:router", prefix="/api")

# -----------------------------------------------------------------------------
# NEW: Mini-app router (user/settings, user/stats, bonus, tables)
# Mounted both ways to work with Nginx that strips /api and with ones that keep it.
# -----------------------------------------------------------------------------

try:
    from app.routers.miniapp import router as miniapp_router  # File 16
except Exception as e:  # pragma: no cover
    logger.error("❌ miniapp router not found: %s", e)
    raise

# Serve without prefix (because your Nginx rewrites /api/x → /x)
app.include_router(miniapp_router)                    # /user/* , /tables/*

# Also serve with /api prefix (works if you change Nginx later to keep /api)
app.include_router(miniapp_router, prefix="/api")     # /api/user/* , /api/tables/*

# -----------------------------------------------------------------------------
# Startup: print routes for quick diagnostics
# -----------------------------------------------------------------------------

@app.on_event("startup")
async def _on_startup() -> None:
    logger.info("📡 Routes registered:")
    for r in app.routes:
        try:
            methods = getattr(r, "methods", {"GET"})
            path = getattr(r, "path")
            if path:
                logger.info("  %s %s", methods, path)
        except Exception:
            continue

# -----------------------------------------------------------------------------
# Local dev runner (container uses `uvicorn app.main:app`)
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), reload=True)
