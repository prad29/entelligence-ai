#!/bin/bash
set -e

# Wait up to 30 seconds for the backend health check to return 200
MAX_RETRIES=6
RETRY_INTERVAL=5

for i in $(seq 1 $MAX_RETRIES); do
  HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost/api/v1/settings/bedrock/status || echo "000")

  if [ "$HTTP_STATUS" = "200" ]; then
    echo "Service healthy (HTTP $HTTP_STATUS) after $i attempt(s)."
    exit 0
  fi

  echo "Attempt $i/$MAX_RETRIES — HTTP $HTTP_STATUS. Retrying in ${RETRY_INTERVAL}s..."
  sleep $RETRY_INTERVAL
done

echo "Service failed health check after $MAX_RETRIES attempts."
exit 1
