# Intelligent Workflow Platform — AWS infrastructure (Terraform).
#
# Provisions: VPC + 2 public subnets + IGW, RDS Postgres (single instance,
# pgvector ready), ECR repo, ECS Fargate cluster + service + task,
# Application Load Balancer (HTTP for now), Secrets Manager entries,
# S3 bucket for the S3 connector, IAM roles with least-privilege bedrock +
# secrets + S3 access.
#
# Idle cost target: ~$50-80/month. The cost-driving items are RDS
# (~$15-20/mo for db.t4g.micro) and ALB (~$22/mo). Fargate is ~$9/mo idle.
# Keeping ECS in public subnets (with security groups) avoids the ~$33/mo
# NAT gateway charge — fine for early-stage. Move to private subnets +
# NAT/VPC endpoints when traffic and threat model justify the cost.
#
# What this stack DOES NOT yet provision:
# - HTTPS / ACM certificate (needs a domain; trivial follow-up).
# - Custom domain (Route 53). Use the ALB DNS until you have one.
# - Multi-AZ RDS. Single AZ for cost; flip `multi_az = true` for prod.
# - Auto-scaling. Single Fargate task; raise `desired_count` and add
#   target-tracking policies when load justifies.
# - WAF / CloudFront. Add when you face the public internet at scale.
# - Tenant isolation (per-tenant DB schemas). Single-tenant for now.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Uncomment + configure once an S3 backend is set up; until then,
  # state lives locally (don't commit terraform.tfstate).
  # backend "s3" {
  #   bucket         = "your-tf-state-bucket"
  #   key            = "workflow-platform/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "your-tf-state-locks"
  # }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project     = "workflow-platform"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "random_password" "db" {
  length  = 32
  special = false
}
