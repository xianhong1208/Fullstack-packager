"""WebSocket endpoints for real-time task updates."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.database import async_session
from app.models.user import User
from app.services.auth import decode_token
from app.services.permission import PermissionCode, get_user_permissions, has_permission
from app.services.task_manager import task_manager

router = APIRouter()


def _extract_ws_token(websocket: WebSocket) -> tuple[str | None, str | None]:
    """Return (token, subprotocol_to_echo).

    Prefer the ``Sec-WebSocket-Protocol`` header (offered as ``bearer, <jwt>``)
    so the JWT never appears in the URL — query-string tokens get written to
    access/proxy logs, which is exactly what a security review flagged. Falls
    back to the legacy ``?token=`` query param so older clients keep working.
    When the header form is used we must echo the ``bearer`` subprotocol back
    on accept, or the browser aborts the handshake.
    """
    proto = websocket.headers.get("sec-websocket-protocol")
    if proto:
        parts = [p.strip() for p in proto.split(",") if p.strip()]
        if len(parts) >= 2 and parts[0] == "bearer":
            return parts[1], "bearer"
    return websocket.query_params.get("token"), None


async def _authenticate_websocket(
    websocket: WebSocket,
) -> tuple[str, set[str], str | None] | None:
    """Validate the JWT and load the user's permissions.

    Returns (username, permissions, subprotocol) on success — subprotocol is
    the value to echo on accept() — or None after closing the socket on
    failure. Mirrors the REST layer's get_current_user.
    """
    token, subprotocol = _extract_ws_token(websocket)
    if not token:
        await websocket.close(code=4401, reason="Missing token")
        return None

    payload = decode_token(token)
    if not payload:
        await websocket.close(code=4401, reason="Invalid or expired token")
        return None

    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        await websocket.close(code=4401, reason="Invalid token subject")
        return None

    async with async_session() as db:
        user = await db.get(User, user_id)
        if not user or not user.is_active:
            await websocket.close(code=4401, reason="Unknown or disabled user")
            return None
        permissions = await get_user_permissions(db, user.id)
        return user.username, permissions, subprotocol


@router.websocket("/ws/tasks")
async def websocket_all_tasks(websocket: WebSocket) -> None:
    """Global task stream. Requires task:view_all (same as GET /api/tasks).

    Auth: offer the JWT via the Sec-WebSocket-Protocol header —
    ``new WebSocket(url, ['bearer', token])`` — not the URL.
    """
    auth = await _authenticate_websocket(websocket)
    if auth is None:
        return
    _username, permissions, subprotocol = auth

    if not has_permission(permissions, PermissionCode.TASK_VIEW_ALL.value):
        await websocket.close(code=4403, reason="task:view_all required")
        return

    await websocket.accept(subprotocol=subprotocol)
    await task_manager.register_websocket(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text('{"type": "pong"}')
    except WebSocketDisconnect:
        pass
    finally:
        await task_manager.unregister_websocket(websocket)


@router.websocket("/ws/tasks/{task_id}")
async def websocket_single_task(websocket: WebSocket, task_id: str) -> None:
    """Per-task stream. Only the task's owner or a task:view_all holder may
    subscribe.

    We do NOT replay the existing log backlog on connect — the client loads
    the current snapshot over HTTP first, then this stream carries only the
    increments produced after connection. That keeps the two sources from
    duplicating each other; a low-frequency HTTP poll reconciles any lines
    missed in the connect window.
    """
    auth = await _authenticate_websocket(websocket)
    if auth is None:
        return
    username, permissions, subprotocol = auth

    # Existence check before accept so unauthorized probing can't distinguish
    # "no task" from "not yours".
    task = await task_manager.get_task(task_id)
    if not task:
        await websocket.close(code=4404, reason="Task not found")
        return

    if task.user_name != username and not has_permission(
        permissions, PermissionCode.TASK_VIEW_ALL.value
    ):
        await websocket.close(code=4403, reason="Not your task")
        return

    await websocket.accept(subprotocol=subprotocol)
    await task_manager.register_websocket(websocket, task_id)

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text('{"type": "pong"}')
    except WebSocketDisconnect:
        pass
    finally:
        await task_manager.unregister_websocket(websocket, task_id)
