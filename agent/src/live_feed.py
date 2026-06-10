"""
Server-Sent Events bridge for the live dashboard.

GET /api/live re-emits everything the debate graph publishes on the Redis
pub/sub channel `atlas:dashboard` (decision conclusions, reliability updates)
as SSE events, with a heartbeat comment every 20s so proxies keep the stream
open. The browser's Executive Overview subscribes and refetches on `decision`
events instead of polling aggressively.

Strictly read-only and additive: this module owns no Redis keys, never writes,
and serves only what the live system actually published — nothing is fabricated.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from redis import asyncio as aioredis

live_router = APIRouter(tags=["live"])

# Mirrors redis_layer.publish(): channels are namespaced under `atlas:`.
_CHANNEL = "atlas:dashboard"
_HEARTBEAT_SECONDS = 20.0


def _client() -> "aioredis.Redis":
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return aioredis.from_url(url, decode_responses=True)


async def _aclose(resource: object) -> None:
    closer = getattr(resource, "aclose", None) or getattr(resource, "close", None)
    if closer is None:
        return
    try:
        result = closer()
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        pass


async def _event_stream() -> AsyncIterator[str]:
    client = _client()
    pubsub = client.pubsub()
    await pubsub.subscribe(_CHANNEL)
    try:
        yield ": connected\n\n"
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=_HEARTBEAT_SECONDS
            )
            if message is None:
                yield ": ping\n\n"
                continue
            if message.get("type") != "message":
                continue
            raw = message.get("data")
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                payload = {"event": "update", "data": str(raw)}
            kind = str(payload.get("event") or "update")
            yield f"event: {kind}\ndata: {json.dumps(payload)}\n\n"
    finally:
        await _aclose(pubsub)
        await _aclose(client)


@live_router.get("/live")
async def live_feed() -> StreamingResponse:
    """Live dashboard event stream (SSE) bridged from Redis pub/sub."""
    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
