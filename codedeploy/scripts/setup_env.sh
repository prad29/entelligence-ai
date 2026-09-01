#!/bin/bash
set -e

mkdir -p /app

DB_SECRET=$(aws secretsmanager get-secret-value --secret-id amenity/db-credentials --query SecretString --output text --region us-east-1)
DB_USER=$(echo "$DB_SECRET" | jq -r .username)
DB_PASS=$(echo "$DB_SECRET" | jq -r .password)
APP_SECRET=$(aws secretsmanager get-secret-value --secret-id amenity/app-secret-key --query SecretString --output text --region us-east-1)
BEDROCK_KEY=$(aws secretsmanager get-secret-value --secret-id amenity/bedrock-api-key --query SecretString --output text --region us-east-1)
SERPER_KEY=$(aws secretsmanager get-secret-value --secret-id amenity/serper-api-key --query SecretString --output text --region us-east-1)
BEDROCK_STATIC_CREDS=$(aws secretsmanager get-secret-value --secret-id amenity/aws-bedrock-keys --query SecretString --output text --region us-east-1)
BEDROCK_STATIC_ACCESS_KEY_ID=$(echo "$BEDROCK_STATIC_CREDS" | jq -r .access_key_id)
BEDROCK_STATIC_SECRET_ACCESS_KEY=$(echo "$BEDROCK_STATIC_CREDS" | jq -r .secret_access_key)
PROD_DB_SECRET=$(aws secretsmanager get-secret-value --secret-id amenity/prod-db-credentials --query SecretString --output text --region us-east-1)
PROD_DB_HOST=$(echo "$PROD_DB_SECRET" | jq -r .host)
PROD_DB_PORT=$(echo "$PROD_DB_SECRET" | jq -r .port)
PROD_DB_DATABASE=$(echo "$PROD_DB_SECRET" | jq -r .database)
PROD_DB_USERNAME=$(echo "$PROD_DB_SECRET" | jq -r .username)
PROD_DB_PASSWORD=$(echo "$PROD_DB_SECRET" | jq -r .password)
EXTERNAL_API_KEY=$(aws secretsmanager get-secret-value --secret-id amenity/external-api-key --query SecretString --output text --region us-east-1)
# /api/v1/lobby-check REUSES this same key (X_API_KEY below) -- no separate
# lobby-check secret. Bedrock auth for lobby-check also reuses BEDROCK_KEY
# below (amenity/bedrock-api-key), not the static AWS_ACCESS_KEY_ID/
# AWS_SECRET_ACCESS_KEY pair -- see app/lobby_check/extractor.py.
# Rotation pool for the Deleted Showtimes Check feature — one JSON secret
# holding all configured SerpApi keys, keyed "1".."13" (slot 1 is the
# legacy single key). Add more slots here (and in app/config.py) if more
# keys are added later; a missing slot in the JSON just yields an empty
# string, which Settings.SERPAPI_API_KEYS filters out.
SERPAPI_KEYS_JSON=$(aws secretsmanager get-secret-value --secret-id amenity/serpapi-api-keys --query SecretString --output text --region us-east-1)
SERPAPI_API_KEY=$(echo "$SERPAPI_KEYS_JSON" | jq -r '."1" // ""')
SERPAPI_API_KEY_2=$(echo "$SERPAPI_KEYS_JSON" | jq -r '."2" // ""')
SERPAPI_API_KEY_3=$(echo "$SERPAPI_KEYS_JSON" | jq -r '."3" // ""')
SERPAPI_API_KEY_4=$(echo "$SERPAPI_KEYS_JSON" | jq -r '."4" // ""')
SERPAPI_API_KEY_5=$(echo "$SERPAPI_KEYS_JSON" | jq -r '."5" // ""')
SERPAPI_API_KEY_6=$(echo "$SERPAPI_KEYS_JSON" | jq -r '."6" // ""')
SERPAPI_API_KEY_7=$(echo "$SERPAPI_KEYS_JSON" | jq -r '."7" // ""')
SERPAPI_API_KEY_8=$(echo "$SERPAPI_KEYS_JSON" | jq -r '."8" // ""')
SERPAPI_API_KEY_9=$(echo "$SERPAPI_KEYS_JSON" | jq -r '."9" // ""')
SERPAPI_API_KEY_10=$(echo "$SERPAPI_KEYS_JSON" | jq -r '."10" // ""')
SERPAPI_API_KEY_11=$(echo "$SERPAPI_KEYS_JSON" | jq -r '."11" // ""')
SERPAPI_API_KEY_12=$(echo "$SERPAPI_KEYS_JSON" | jq -r '."12" // ""')
SERPAPI_API_KEY_13=$(echo "$SERPAPI_KEYS_JSON" | jq -r '."13" // ""')

cat > /app/.env.prod <<EOF
DATABASE_URL=postgresql://${DB_USER}:${DB_PASS}@amenity-db.critf4jd3ef7.us-east-1.rds.amazonaws.com:5432/amenitydb
SECRET_KEY=${APP_SECRET}
BEDROCK_REGION=us-east-1
BEDROCK_MODEL_ID=mistral.mistral-large-3-675b-instruct
BEDROCK_API_KEY=${BEDROCK_KEY}
AI_TRIGGER_MODE=on
REDIS_URL=redis://redis:6379/0
BEDROCK_CACHE_TTL_DAYS=30
MAX_BATCH_ROWS=10000
JOB_TTL_HOURS=24
TRACK_C_MIN_LEN=4
CIRCUIT_MATCH_MIN_JACCARD=0.5
VESPA_URL=http://vespa:8080
SEMANTIC_SEARCH_ENABLED=true
EMBEDDING_MODEL_ID=cohere.embed-multilingual-v3
CLAUDE_SANDBOX_URL=http://claude-sandbox:3100
SERPER_API_KEY=${SERPER_KEY}
AGENTIC_TITLE_MATCH_ENABLED=true
AGENTIC_USE_BEDROCK=true
AGENTIC_CLAUDE_MODEL=us.anthropic.claude-sonnet-5
AGENTIC_TIMEOUT_SECONDS=150
AGENTIC_BATCH_MAX_CONCURRENCY=4
AGENTIC_BATCH_S3_BUCKET=erica-datastore
AGENTIC_BATCH_S3_REGION=us-east-1
CLAUDE_CODE_USE_BEDROCK=1
AWS_REGION=us-east-1
AWS_DEFAULT_REGION=us-east-1
AWS_ACCESS_KEY_ID=${BEDROCK_STATIC_ACCESS_KEY_ID}
AWS_SECRET_ACCESS_KEY=${BEDROCK_STATIC_SECRET_ACCESS_KEY}
PROD_DB_HOST=${PROD_DB_HOST}
PROD_DB_PORT=${PROD_DB_PORT}
PROD_DB_DATABASE=${PROD_DB_DATABASE}
PROD_DB_USERNAME=${PROD_DB_USERNAME}
PROD_DB_PASSWORD=${PROD_DB_PASSWORD}
EXTERNAL_API_ENABLED=true
X_API_KEY=${EXTERNAL_API_KEY}
LOBBY_CHECK_ENABLED=false
LOBBY_CHECK_MODEL_ID=qwen.qwen3-vl-235b-a22b
LOBBY_CHECK_ALLOWED_URL_HOSTS=mm-intelligence.s3.amazonaws.com
LOBBY_CHECK_MAX_BATCH_ROWS=500
LOBBY_CHECK_MAX_CONCURRENCY=4
LOBBY_CHECK_TIMEOUT_SECONDS=90
LOBBY_CHECK_ROW_MAX_ATTEMPTS=3
LOBBY_CHECK_JOB_TTL_HOURS=72
SERPAPI_API_KEY=${SERPAPI_API_KEY}
SERPAPI_API_KEY_2=${SERPAPI_API_KEY_2}
SERPAPI_API_KEY_3=${SERPAPI_API_KEY_3}
SERPAPI_API_KEY_4=${SERPAPI_API_KEY_4}
SERPAPI_API_KEY_5=${SERPAPI_API_KEY_5}
SERPAPI_API_KEY_6=${SERPAPI_API_KEY_6}
SERPAPI_API_KEY_7=${SERPAPI_API_KEY_7}
SERPAPI_API_KEY_8=${SERPAPI_API_KEY_8}
SERPAPI_API_KEY_9=${SERPAPI_API_KEY_9}
SERPAPI_API_KEY_10=${SERPAPI_API_KEY_10}
SERPAPI_API_KEY_11=${SERPAPI_API_KEY_11}
SERPAPI_API_KEY_12=${SERPAPI_API_KEY_12}
SERPAPI_API_KEY_13=${SERPAPI_API_KEY_13}
DELETED_SHOWTIME_S3_BUCKET=erica-datastore
DELETED_SHOWTIME_S3_REGION=us-east-1
DELETED_SHOWTIME_MAX_ROWS=1000
DELETED_SHOWTIME_JOB_TTL_HOURS=720
DELETED_SHOWTIME_ABORT_AFTER=5
EOF

chmod 600 /app/.env.prod
echo "setup_env done"
