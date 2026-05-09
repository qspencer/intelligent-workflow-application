# Secrets Manager entries the backend reads at startup via the SecretStore
# abstraction. Add more secrets here (OIDC client config, third-party API
# keys, webhook HMAC keys, etc.) as connectors are wired up.

resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${var.project_name}/${var.environment}/database_url"
  description             = "Async Postgres URL for the backend"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id     = aws_secretsmanager_secret.database_url.id
  secret_string = "postgresql+asyncpg://${aws_db_instance.this.username}:${random_password.db.result}@${aws_db_instance.this.endpoint}/${aws_db_instance.this.db_name}?ssl=require"
}
