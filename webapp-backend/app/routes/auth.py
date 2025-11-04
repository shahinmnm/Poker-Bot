from fastapi import APIRouter, Request, Response
from app.dependencies import get_or_create_session

router = APIRouter()


@router.post("/login")
async def login(request: Request, response: Response):
    """Auto-login endpoint - creates session."""
    session = get_or_create_session(request)

    # Set session cookie
    response.set_cookie(
        key="session_id",
        value=session["session_id"],
        httponly=True,
        max_age=86400,  # 24 hours
        samesite="lax",
    )

    return {
        "success": True,
        "user_id": session["user_id"],
        "username": session["username"],
    }


@router.get("/me")
async def get_current_user_info(request: Request):
    """Get current user info."""
    session = get_or_create_session(request)
    return {"user_id": session["user_id"], "username": session["username"]}
