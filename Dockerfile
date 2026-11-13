FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# No command is enabled until runtime configuration and the application lifespan
# are wired in the implementation milestone.
CMD ["python", "-m", "payments_mcp.server"]
