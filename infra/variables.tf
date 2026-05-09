variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment name (e.g. dev, staging, prod)."
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Resource name prefix."
  type        = string
  default     = "workflow-platform"
}

variable "container_image" {
  description = <<-EOT
    Docker image URI to run on Fargate. After `terraform apply` builds the ECR
    repo, push the backend image with `infra/scripts/build_and_push.sh` and set
    this to the resulting tag. The first apply uses a placeholder; the service
    will fail to start until you push a real image.
  EOT
  type        = string
  default     = "public.ecr.aws/docker/library/python:3.12-slim"
}

variable "container_cpu" {
  description = "Fargate task CPU (256 = 0.25 vCPU)."
  type        = number
  default     = 512
}

variable "container_memory" {
  description = "Fargate task memory (MB)."
  type        = number
  default     = 1024
}

variable "desired_count" {
  description = "Number of Fargate tasks. Raise + add autoscaling under load."
  type        = number
  default     = 1
}

variable "db_instance_class" {
  description = "RDS instance class. db.t4g.micro is the cheapest option that runs pgvector."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_storage_gb" {
  description = "RDS allocated storage."
  type        = number
  default     = 20
}

variable "db_postgres_version" {
  description = "Postgres engine version. 16.x supports pgvector via CREATE EXTENSION vector;"
  type        = string
  default     = "16.4"
}

variable "auth_mode" {
  description = "AUTH_MODE env var the backend reads. `dev` for header-based auth in non-prod; `oidc` for production."
  type        = string
  default     = "dev"
}

variable "bedrock_model_arns" {
  description = <<-EOT
    Bedrock model / inference-profile ARNs the task role can invoke. Default
    grants InvokeModel on Anthropic models in the current account. Tighten to
    a specific profile ARN for production.
  EOT
  type        = list(string)
  default     = ["arn:aws:bedrock:*::foundation-model/anthropic.*"]
}

variable "log_retention_days" {
  description = "CloudWatch log retention."
  type        = number
  default     = 14
}
