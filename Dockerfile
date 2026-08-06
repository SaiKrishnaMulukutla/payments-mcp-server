FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[redis,auth]"

EXPOSE 8000
# Default: stdio. Set PAYMENTS_TRANSPORT=streamable-http for HTTP; Render's injected $PORT is honored.
CMD ["sh", "-c", "PAYMENTS_HOST=0.0.0.0 PAYMENTS_PORT=${PORT:-8000} python -m payments_mcp.server"]
