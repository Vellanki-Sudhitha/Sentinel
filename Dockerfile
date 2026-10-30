# Build the React dashboard first...
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ...then bundle it with the FastAPI backend.
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SENTINEL_HOST=0.0.0.0 \
    SENTINEL_PORT=8000 \
    DATABASE_PATH=/data/sentinel.db

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY --from=frontend /build/dist ./frontend/dist

RUN mkdir -p /data
EXPOSE 8000
VOLUME ["/data"]

CMD ["sh", "-c", "uvicorn app.main:app --host ${SENTINEL_HOST} --port ${SENTINEL_PORT}"]
