"""Admin authentication.

Admin endpoints require the shared admin key ``LLM_GATEWAY_ADMIN_KEY``.
"""
from __future__ import annotations

import os

from fastapi import HTTPException, Request


def require_admin(request: Request) -> str:
    admin_key = os.environ.get("LLM_GATEWAY_ADMIN_KEY")
    if not admin_key:
        raise HTTPException(
            status_code=503,
            detail="admin API disabled: LLM_GATEWAY_ADMIN_KEY not configured",
        )
    provided = _extract_api_key(request)
    if not provided or provided != admin_key:
        raise HTTPException(status_code=401, detail="invalid admin key")
    return admin_key


def _extract_api_key(request: Request) -> str | None:
    auth = request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[len("bearer "):].strip()
    return request.headers.get("x-api-key")
