output "ecr_repository_url" {
  description = "Push backend images here."
  value       = aws_ecr_repository.backend.repository_url
}

output "alb_dns_name" {
  description = "Public ALB DNS. HTTP only until an ACM cert is added."
  value       = aws_lb.this.dns_name
}

output "rds_endpoint" {
  description = "RDS Postgres endpoint."
  value       = aws_db_instance.this.endpoint
}

output "s3_bucket_name" {
  description = "Default S3 bucket for the S3Connector."
  value       = aws_s3_bucket.storage.bucket
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "ecs_service_name" {
  value = aws_ecs_service.backend.name
}

output "task_role_arn" {
  description = "Application's IAM role (Bedrock + S3 + secrets)."
  value       = aws_iam_role.task_role.arn
}
