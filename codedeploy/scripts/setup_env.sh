#!/bin/bash
set -e

mkdir -p /app

DB_SECRET=$(aws secretsmanager get-secret-value --secret-id amenity/db-credentials --query SecretString --output text --region us-east-1)
DB_USER=$(echo "$DB_SECRET" | jq -r .username)
DB_PASS=$(echo "$DB_SECRET" | jq -r .password)
APP_SECRET=$(aws secretsmanager get-secret-value --secret-id amenity/app-secret-key --query SecretString --output text --region us-east-1)
BEDROCK_KEY=$(aws secretsmanager get-secret-value --secret-id amenity/bedrock-api-key --query SecretString --output text --region us-east-1)

cat > /app/.env.prod <<EOF
DATABASE_URL=postgresql://${DB_USER}:${DB_PASS}@amenity-db.critf4jd3ef7.us-east-1.rds.amazonaws.com:5432/amenitydb
SECRET_KEY=${APP_SECRET}
BEDROCK_REGION=us-east-1
BEDROCK_MODEL_ID=mistral.mistral-large-2407-v1:0
BEDROCK_API_KEY=${BEDROCK_KEY}
AI_TRIGGER_MODE=on
REDIS_URL=redis://redis:6379/0
BEDROCK_CACHE_TTL_DAYS=30
MAX_BATCH_ROWS=10000
JOB_TTL_HOURS=24
TRACK_C_MIN_LEN=4
CIRCUIT_MATCH_MIN_JACCARD=0.5
EOF

chmod 600 /app/.env.prod
echo "setup_env done"
