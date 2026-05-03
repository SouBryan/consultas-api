#!/bin/bash
set -euo pipefail

cd /opt/consultas-api
git pull
docker compose down
docker compose up -d --build
docker compose logs -f --tail=50