#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

QWEN_CONTAINER="${QWEN_CONTAINER:-qwen3-35b-a3b-fp8-vllm-1234}"
QWEN_API_BASE="${QWEN_API_BASE:-http://10.144.133.1:1234/v1}"
QWEN_MODEL="${QWEN_MODEL:-qwen3.6-35b-a3b-fp8}"
IONE_GATEWAY_BIND="${IONE_GATEWAY_BIND:-10.144.133.1}"

gateway_token="$(openssl rand -hex 32)"
qwen_key="$(
	docker inspect "${QWEN_CONTAINER}" \
		| python3 -c 'import json,sys; print(next(x.split("=",1)[1] for x in json.load(sys.stdin)[0]["Config"]["Env"] if x.startswith("VLLM_API_KEY=")))'
)"

test -n "${qwen_key}"
umask 077
printf '%s\n' \
	"IONE_GATEWAY_TOKEN=${gateway_token}" \
	"IONE_GATEWAY_BIND=${IONE_GATEWAY_BIND}" \
	"IONE_GATEWAY_DOCKERFILE=${IONE_GATEWAY_DOCKERFILE:-Dockerfile}" \
	"QWEN_API_BASE=${QWEN_API_BASE}" \
	"QWEN_API_KEY=${qwen_key}" \
	"QWEN_MODEL=${QWEN_MODEL}" \
	"UFO_MAX_ROUNDS=10" \
	"UFO_MAX_STEP=15" \
	'UFO_DEVICES_JSON={"devices": []}' > .env
chmod 600 .env

echo "Gateway configuration created at $(pwd)/.env"
