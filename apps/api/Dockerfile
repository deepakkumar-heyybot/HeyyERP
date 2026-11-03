# The API image. Build from apps/api:  docker build -t heyyerp-api .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install .

RUN useradd --system --uid 10001 heyyerp
USER heyyerp
EXPOSE 8000

# Migrations are a separate, deliberate step (see docs/DEPLOYMENT_AND_OPERATIONS.md), never run at start.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/health/live').status==200 else 1)"
CMD ["uvicorn", "heyyerp_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
