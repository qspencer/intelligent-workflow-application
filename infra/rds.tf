# Single-instance RDS Postgres. Postgres 16+ supports pgvector via
# `CREATE EXTENSION vector;` after the DB is up. The `rds.force_ssl` parameter
# below requires SSL connections from the application — match in DATABASE_URL.

resource "aws_db_subnet_group" "this" {
  name       = "${var.project_name}-${var.environment}"
  subnet_ids = aws_subnet.public[*].id
}

resource "aws_db_parameter_group" "postgres16" {
  name   = "${var.project_name}-${var.environment}-pg16"
  family = "postgres16"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "this" {
  identifier              = "${var.project_name}-${var.environment}"
  engine                  = "postgres"
  engine_version          = var.db_postgres_version
  instance_class          = var.db_instance_class
  allocated_storage       = var.db_storage_gb
  storage_type            = "gp3"
  storage_encrypted       = true
  username                = "workflow"
  password                = random_password.db.result
  db_name                 = "workflow"
  parameter_group_name    = aws_db_parameter_group.postgres16.name
  db_subnet_group_name    = aws_db_subnet_group.this.name
  vpc_security_group_ids  = [aws_security_group.rds.id]
  multi_az                = false # flip to true for prod
  publicly_accessible     = false
  skip_final_snapshot     = true  # set false in prod
  deletion_protection     = false # set true in prod
  backup_retention_period = 7
  apply_immediately       = true
}
