#!/usr/bin/env bash
# Build + push the backend Docker image to the ECR repo created by Terraform.
#
# Usage:
#     cd infra && terraform apply             # creates the ECR repo
#     bash scripts/build_and_push.sh [tag]    # default tag = `latest`
#     terraform apply -var "container_image=<repo-url>:<tag>"
#     aws ecs update-service --cluster ... --service ... --force-new-deployment
#
# Requires: docker, aws cli, jq.

set -euo pipefail

TAG="${1:-latest}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$INFRA_DIR")"

cd "$INFRA_DIR"
REPO_URL="$(terraform output -raw ecr_repository_url)"
REGION="$(terraform output -raw -no-color region 2>/dev/null || echo us-east-1)"
ACCOUNT_ID="${REPO_URL%%.dkr.*}"

echo "Building $REPO_URL:$TAG from $REPO_ROOT/backend"
docker build -t "$REPO_URL:$TAG" "$REPO_ROOT/backend"

echo "Logging in to ECR ($REGION)"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

echo "Pushing $REPO_URL:$TAG"
docker push "$REPO_URL:$TAG"

echo
echo "Image pushed. Next steps:"
echo "  cd $INFRA_DIR"
echo "  terraform apply -var \"container_image=$REPO_URL:$TAG\""
echo "  aws ecs update-service --cluster \$(terraform output -raw ecs_cluster_name) \\"
echo "    --service \$(terraform output -raw ecs_service_name) --force-new-deployment"
