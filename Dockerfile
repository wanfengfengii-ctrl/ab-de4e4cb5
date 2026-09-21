# syntax=docker/dockerfile:1

# ---- stage 1: build the React console --------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- stage 2: FastAPI API + static assets ----------------------------------
FROM python:3.12-slim AS runtime
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STATIC_DIR=/app/static

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=frontend /build/dist ./static

EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=4s --start-period=5s --retries=12 \
  CMD python -c "import json,sys,urllib.request as u; r=u.urlopen('http://localhost:8000/health',timeout=3); sys.exit(0 if r.status==200 and json.load(r).get('status')=='ok' else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
