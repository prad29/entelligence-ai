#!/bin/bash
set -e

cd /app

IMAGE_TAG=$(cat /app/image_tag.txt)
ECR_BACKEND=$(aws ssm get-parameter --name /amenity/ecr_backend --query Parameter.Value --output text)

# Pull the image first so the migration run uses the exact same image that will serve traffic
aws ecr get-login-password --region $AWS_DEFAULT_REGION \
  | docker login --username AWS --password-stdin \
    "$(echo $ECR_BACKEND | cut -d/ -f1)"

docker pull $ECR_BACKEND:$IMAGE_TAG

# Run alembic migrations before switching traffic
docker run --rm \
  --env-file /app/.env.prod \
  $ECR_BACKEND:$IMAGE_TAG \
  alembic upgrade head

echo "Migrations complete."
