"""
Simple token-based authentication for JARVIS.
Supports both Authorization header (Bearer) and ?token= query parameter.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, WebSocket, status

from config import settings


# ─────────────────────────────────────────────────────────────────────────────
# Core verification
# ─────────────────────────────────────────────────────────────────────────────

def verify_token(token: str) -> bool:
    """Return True if token matches the configured secret."""
    if not token:
        return False
    return token.strip() == settings.SECRET_TOKEN


def get_token_from_request(request: Request) -> str | None:
    """
    Extract the bearer token from:
      1. Authorization: Bearer <token>  header
      2. ?token=<token>                 query parameter
    Returns the raw token string or None.
    """
    # Header check
    auth_header: str = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):]

    # Query parameter fallback
    return request.query_params.get("token")


def get_token_from_websocket(websocket: WebSocket) -> str | None:
    """Same extraction logic for WebSocket upgrade requests."""
    auth_header: str = websocket.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):]
    return websocket.query_params.get("token")


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI dependency
# ─────────────────────────────────────────────────────────────────────────────

async def require_auth(request: Request) -> None:
    """
    FastAPI dependency that raises HTTP 401 when the request is unauthenticated.

    Usage:
        @router.get("/secure", dependencies=[Depends(require_auth)])
    """
    token = get_token_from_request(request)
    if not verify_token(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_ws_auth(websocket: WebSocket) -> None:
    """WebSocket variant — closes the socket with 1008 if auth fails."""
    token = get_token_from_websocket(websocket)
    if not verify_token(token):
        await websocket.close(code=1008)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
