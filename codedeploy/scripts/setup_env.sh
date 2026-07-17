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
AGENTIC_BATCH_MAX_CONCURRENCY=2
AGENTIC_BATCH_S3_BUCKET=erica-datastore
AGENTIC_BATCH_S3_REGION=us-east-1
CLAUDE_CODE_USE_BEDROCK=1
AWS_REGION=us-east-1
AWS_DEFAULT_REGION=us-east-1
AWS_ACCESS_KEY_ID=${BEDROCK_STATIC_ACCESS_KEY_ID}
AWS_SECRET_ACCESS_KEY=${BEDROCK_STATIC_SECRET_ACCESS_KEY}
EOF

chmod 600 /app/.env.prod
echo "setup_env done"
