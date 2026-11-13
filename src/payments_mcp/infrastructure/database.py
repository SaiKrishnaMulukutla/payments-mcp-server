"""PostgreSQL connection-pool lifecycle; no alternate storage backend."""

from psycopg_pool import AsyncConnectionPool

from payments_mcp.config import Settings


def create_pool(settings: Settings) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=settings.database_url.get_secret_value(),
        min_size=settings.database_pool_min_size,
        max_size=settings.database_pool_max_size,
        open=False,
    )
