#!/bin/bash
# Refreshes dqscan's code, venv, env file, and cron entry on every backend
# deploy. Runs as an AfterInstall hook alongside setup_env.sh/run_migrations.sh,
# but unlike those, a failure in here must NEVER fail this hook -- dqscan is
# not part of the app being deployed, so a dqscan regression (bad deps,
# missing secret, whatever) should be logged and skipped, not roll back a
# backend release. Everything risky is wrapped in one block; we always exit 0.
{
  set -uo pipefail

  DQSCAN_HOME=/opt/entelligence-ai
  VENV="$DQSCAN_HOME/dqscan-venv"
  LOG_DIR=/var/log/dqscan
  REGION=us-east-1

  mkdir -p "$LOG_DIR"
  chown ec2-user:ec2-user "$LOG_DIR"
  chown -R ec2-user:ec2-user "$DQSCAN_HOME/dqscan" "$DQSCAN_HOME/config.yaml" "$DQSCAN_HOME/config.prod.yaml"

  # DB credentials come from Secrets Manager, not the repo -- same pattern as
  # setup_env.sh's PROD_DB_SECRET. enttelligence-ai/dev-db-credentials and
  # amenity/prod-db-credentials must exist and the EC2 role must be able to
  # read them (see infra/amenity-all.yml's DqscanAccess policy).
  DEV_DB_SECRET=$(aws secretsmanager get-secret-value --secret-id enttelligence-ai/dev-db-credentials --query SecretString --output text --region "$REGION")
  PROD_DB_SECRET=$(aws secretsmanager get-secret-value --secret-id amenity/prod-db-credentials --query SecretString --output text --region "$REGION")

  DEV_DB_HOST=$(echo "$DEV_DB_SECRET" | jq -r .host)
  DEV_DB_PORT=$(echo "$DEV_DB_SECRET" | jq -r .port)
  DEV_DB_DATABASE=$(echo "$DEV_DB_SECRET" | jq -r .database)
  DEV_DB_USERNAME=$(echo "$DEV_DB_SECRET" | jq -r .username)
  DEV_DB_PASSWORD=$(echo "$DEV_DB_SECRET" | jq -r .password)

  PROD_DB_HOST=$(echo "$PROD_DB_SECRET" | jq -r .host)
  PROD_DB_PORT=$(echo "$PROD_DB_SECRET" | jq -r .port)
  PROD_DB_DATABASE=$(echo "$PROD_DB_SECRET" | jq -r .database)
  PROD_DB_USERNAME=$(echo "$PROD_DB_SECRET" | jq -r .username)
  PROD_DB_PASSWORD=$(echo "$PROD_DB_SECRET" | jq -r .password)

  # dqscan-only override (2026-09-24): amenity/prod-db-credentials' .database
  # is mqproduction, title_matching's Movie Master schema -- movies_shows
  # itself lives in moviemeasure on this same prod host. Hardcoded rather
  # than a new secret field since it doesn't vary by deployment. See
  # config.prod.yaml.
  DQSCAN_PROD_DB_DATABASE=moviemeasure

  # No AWS_ACCESS_KEY_ID/SECRET here -- boto3 in emailer.py/sns_notifier.py
  # picks up the instance's IAM role automatically.
  cat > "$DQSCAN_HOME/.env" <<EOF
DEV_DB_HOST=${DEV_DB_HOST}
DEV_DB_PORT=${DEV_DB_PORT}
DEV_DB_DATABASE=${DEV_DB_DATABASE}
DEV_DB_USERNAME=${DEV_DB_USERNAME}
DEV_DB_PASSWORD=${DEV_DB_PASSWORD}
PROD_DB_HOST=${PROD_DB_HOST}
PROD_DB_PORT=${PROD_DB_PORT}
PROD_DB_DATABASE=${PROD_DB_DATABASE}
PROD_DB_USERNAME=${PROD_DB_USERNAME}
PROD_DB_PASSWORD=${PROD_DB_PASSWORD}
DQSCAN_PROD_DB_DATABASE=${DQSCAN_PROD_DB_DATABASE}
EOF
  chown ec2-user:ec2-user "$DQSCAN_HOME/.env"
  chmod 600 "$DQSCAN_HOME/.env"

  if [ ! -d "$VENV" ]; then
    sudo -u ec2-user python3 -m venv "$VENV"
  fi
  sudo -u ec2-user "$VENV/bin/pip" install --quiet --upgrade pip
  sudo -u ec2-user "$VENV/bin/pip" install --quiet -r "$DQSCAN_HOME/dqscan/requirements.txt"

  CRON_LINE="0 18 * * * cd $DQSCAN_HOME && $VENV/bin/python -m dqscan.run_daily --env dev >> $LOG_DIR/cron.log 2>&1"
  ( sudo -u ec2-user crontab -l 2>/dev/null | grep -vF "dqscan.run_daily" ; echo "$CRON_LINE" ) | sudo -u ec2-user crontab -

  echo "dqscan setup refreshed"
} || echo "dqscan setup failed (non-fatal, backend deploy continues) -- see log above" >&2

exit 0
