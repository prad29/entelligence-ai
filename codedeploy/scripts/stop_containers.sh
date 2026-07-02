#!/bin/bash
set -e

cd /app

# Stop and remove existing containers gracefully.
# Ignore error if no containers are running (first deploy).
docker compose -f docker-compose.prod.yml down --remove-orphans || true
