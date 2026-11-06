"""Internal gateway -> executor HTTP channel (trusted network only). Timeouts are mandatory."""
from __future__ import annotations
from typing import Any
import httpx
from swarmguard.execution.remote_backend import ChannelError


class HttpExecutorChannel:
    def __init__(self, base_url: str, *, timeout: float = 5.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout, connect=min(timeout, 2.0))
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=self._timeout, follow_redirects=False)

    async def execute(self, grant: str, correlation_id: str) -> tuple[int, dict[str, Any]]:
        try:
            async with self._client() as client:
                response = await client.post(f"{self._base}/internal/v1/execute", json={},
                                             headers={"Authorization": f"Bearer {grant}",
                                                      "X-Correlation-ID": correlation_id})
        except httpx.TransportError as exc:
            raise ChannelError(type(exc).__name__) from None
        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {}
        return response.status_code, body if isinstance(body, dict) else {}

    async def live(self) -> bool:
        try:
            async with self._client() as client:
                return (await client.get(f"{self._base}/health/live")).status_code == 200
        except httpx.TransportError:
            return False
