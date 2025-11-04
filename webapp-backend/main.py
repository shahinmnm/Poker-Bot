#!/usr/bin/env python3
"""Application entrypoint for running the FastAPI app with Uvicorn."""

from app.main import app

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
