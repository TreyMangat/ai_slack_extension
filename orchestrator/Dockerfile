FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps (kept minimal)
RUN apt-get update && apt-get install -y --no-install-recommends git nodejs npm ca-certificates curl && rm -rf /var/lib/apt/lists/*
RUN npm install -g n && n 22.12.0
RUN npm install -g openclaw@latest
RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic

RUN useradd --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
    && mkdir -p /tmp/feature_factory_workspaces \
    && chown -R app:app /app /tmp/feature_factory_workspaces

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
