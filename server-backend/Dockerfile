# ---- étape 1 : installation des dépendances dans un venv isolé ----
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.txt .
RUN /opt/venv/bin/pip install -r requirements.txt

# ---- étape 2 : image finale minimale (pas de compilateur, pas de pip cache) ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH

# Utilisateur non-root sans shell : exigence CYBER (§2 « isolation stricte des privilèges »)
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser \
 && mkdir -p /data/models && chown 10001:10001 /data/models

COPY --from=build /opt/venv /opt/venv
WORKDIR /app
# code possédé par root : l'application ne peut pas se modifier elle-même
COPY app ./app

USER 10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--log-level", "info"]
