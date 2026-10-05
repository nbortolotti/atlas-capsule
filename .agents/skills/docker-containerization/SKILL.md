---
name: docker-containerization
description: "Best practices for writing, optimizing, multi-staging, and debugging Dockerfiles, container images, and container runtimes. Use when building, testing, or configuring containerized applications and services."
---

# Docker Containerization Best Practices

## Multi-Stage Builds

Separate build dependencies (Node/npm, compilers, test tools) from lightweight runtime environments (Python slim, Alpine, distroless) to produce small, secure production images.

```dockerfile
# Stage 1: Build frontend assets
FROM node:20-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Runtime environment
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY config/ ./config/
COPY src/ ./src/
COPY --from=frontend-builder /frontend/dist ./frontend/dist

EXPOSE 8080
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

## Optimization & Security Guidelines

- **Order Layers by Change Frequency**: Put infrequently changing layers (`package.json`, `requirements.txt`, system packages) near the top to maximize layer caching.
- **Minimal Image Footprint**: Use `.dockerignore` to exclude `.git`, `node_modules`, `__pycache__`, local `.env`, and virtualenvs.
- **Clean Cache in Same Layer**: Always chain package installations with cache cleanup (`rm -rf /var/lib/apt/lists/*` or `pip install --no-cache-dir`).
- **Listen on Standard Port**: Support dynamic port assignment via the `PORT` environment variable (default `8080` for Cloud Run compatibility).
