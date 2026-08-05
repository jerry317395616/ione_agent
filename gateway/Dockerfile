FROM python:3.10-slim

ARG UFO_REPOSITORY=https://github.com/microsoft/UFO.git
ARG UFO_BRANCH=main

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UFO_ROOT=/opt/UFO \
    IONE_GATEWAY_DATA_DIR=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 libmagic1 \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${UFO_BRANCH}" "${UFO_REPOSITORY}" "${UFO_ROOT}"

WORKDIR /srv/ione-ufo-gateway
COPY ufo-gateway-requirements.txt ./ufo-gateway-requirements.txt
COPY requirements.txt ./requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip pip install -r ufo-gateway-requirements.txt \
    && pip install -r requirements.txt \
    && cd "${UFO_ROOT}" \
    && PYTHONPATH="${UFO_ROOT}" python -c "from galaxy.galaxy_client import GalaxyClient; from galaxy.webui.websocket_observer import EventSerializer"
COPY app ./app

RUN mkdir -p /data
EXPOSE 8098

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8098/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8098", "--workers", "1", "--no-access-log"]
