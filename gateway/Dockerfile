FROM python:3.11-slim

ARG UFO_REPOSITORY=https://github.com/microsoft/UFO.git
ARG UFO_BRANCH=main

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UFO_ROOT=/opt/UFO \
    IONE_GATEWAY_DATA_DIR=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 libmagic1 \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${UFO_BRANCH}" "${UFO_REPOSITORY}" "${UFO_ROOT}" \
    && pip install --no-cache-dir -r "${UFO_ROOT}/requirements.txt"

WORKDIR /srv/ione-ufo-gateway
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

RUN mkdir -p /data
EXPOSE 8098

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8098/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8098", "--workers", "1"]

