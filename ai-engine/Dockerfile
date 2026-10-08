# ---- étape 1 : dépendances (torch CPU uniquement : pas de CUDA inutile) ----
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
RUN pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
# --no-deps : requirements.txt est la liste complète figée. On évite ainsi qu'ultralytics
# tire opencv-python (version graphique, dépend de libGL) en plus de la version headless.
RUN pip install --no-deps -r requirements.txt

# ---- étape 2 : image d'exécution ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH \
    HOME=/tmp YOLO_CONFIG_DIR=/tmp/ultralytics MPLCONFIGDIR=/tmp/matplotlib YOLO_OFFLINE=1 \
    MODEL_DIR=/app/models FACES_DIR=/app/faces

RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10002 --no-create-home --shell /usr/sbin/nologin vision

COPY --from=build /opt/venv /opt/venv
WORKDIR /app
# Modèles embarqués à la construction et vérifiés : jamais téléchargés à l'exécution
COPY models/ ./models/
RUN cd models && sha256sum -c models.sha256
COPY main.py .

USER 10002
EXPOSE 5000
HEALTHCHECK --interval=20s --timeout=3s --start-period=90s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=2).status == 200 else 1)"]
# 1 seul worker : un seul processus doit posséder la caméra ; les threads servent les flux
CMD ["gunicorn", "--workers", "1", "--worker-class", "gthread", "--threads", "8", "--bind", "0.0.0.0:5000", \
     "--worker-tmp-dir", "/tmp", "--timeout", "120", "--graceful-timeout", "5", "main:app"]
