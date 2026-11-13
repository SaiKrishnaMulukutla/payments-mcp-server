"""Cashfree COU HTTP client boundary.

No endpoint, header, signature or payload is guessed here. Implement operations only from the
current partner API specification supplied by Cashfree and verified in sandbox.
"""

import httpx


class CashfreeClient:
    def __init__(
        self,
        *,
        base_url: str,
        client_id: str,
        client_secret: str,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.AsyncClient(base_url=self._base_url, timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self._http.aclose()

    # Add typed methods only after Cashfree provides the current COU contract.
