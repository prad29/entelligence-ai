#!/bin/bash
set -e

cd /app

AWS_DEFAULT_REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)
IMAGE_TAG=$(cat /app/image_tag.txt)
ECR_BACKEND=$(aws ssm get-parameter --name /amenity/ecr_backend --region $AWS_DEFAULT_REGION --query Parameter.Value --output text)
ECR_FRONTEND=$(aws ssm get-parameter --name /amenity/ecr_frontend --region $AWS_DEFAULT_REGION --query Parameter.Value --output text)

# Pull both images
aws ecr get-login-password --region $AWS_DEFAULT_REGION \
  | docker login --username AWS --password-stdin \
    "$(echo $ECR_BACKEND | cut -d/ -f1)"

docker pull $ECR_BACKEND:$IMAGE_TAG
docker pull $ECR_FRONTEND:$IMAGE_TAG

# Write compose env file so docker-compose.prod.yml resolves image tags
cat > /app/.env.compose << EOF
ECR_BACKEND=${ECR_BACKEND}
ECR_FRONTEND=${ECR_FRONTEND}
IMAGE_TAG=${IMAGE_TAG}
EOF

# Start all services
docker compose -f docker-compose.prod.yml --env-file .env.compose up -d

# Clean up old images to free disk space
docker image prune -f

echo "Containers started with image tag: $IMAGE_TAG"
