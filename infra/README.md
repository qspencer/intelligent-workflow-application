# Infrastructure (Terraform)

AWS infrastructure for the Intelligent Workflow Platform. Provisions VPC + ECS Fargate + RDS Postgres + ALB + S3 + Secrets Manager + IAM + ECR. Sized for low-traffic dev (~$50–80/month idle); flip the documented knobs for production.

## What's here

```
infra/
├── main.tf                 Provider, terraform block (S3 backend commented)
├── variables.tf            Inputs (region, sizes, image, etc.)
├── outputs.tf              ECR repo URL, ALB DNS, RDS endpoint, S3 bucket
├── networking.tf           VPC + 2 public subnets + IGW + security groups
├── ecr.tf                  Image registry (with 10-image lifecycle policy)
├── rds.tf                  Postgres 16, gp3, force_ssl, ready for pgvector
├── secrets.tf              Secrets Manager: database_url
├── s3.tf                   Default storage bucket (versioned, encrypted, private)
├── iam.tf                  Task execution role + task role (Bedrock + S3 + secrets)
├── ecs.tf                  Cluster + task definition + service + ALB
├── terraform.tfvars.example
└── scripts/
    ├── build_and_push.sh   Build the backend image, push to ECR
    └── post_apply_setup.sh Enable pgvector, run Alembic migrations
```

## First apply (cold start)

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars if defaults aren't right for you

terraform init
terraform plan -out plan.tfplan      # always review before applying
terraform apply plan.tfplan
```

The first apply runs the backend with a placeholder Python image (it'll start, fail health-checks, and the service will keep retrying — that's expected). Then push the real image:

```bash
bash scripts/build_and_push.sh        # tags as "latest" by default
terraform apply -var "container_image=$(terraform output -raw ecr_repository_url):latest"
aws ecs update-service \
  --cluster $(terraform output -raw ecs_cluster_name) \
  --service $(terraform output -raw ecs_service_name) \
  --force-new-deployment
```

Then enable pgvector and run migrations:

```bash
bash scripts/post_apply_setup.sh
```

The ALB DNS is in `terraform output alb_dns_name`. Hit `http://$ALB/api/health` once the service is healthy.

## What "production" needs that this doesn't include

| Concern | Status | What to add |
|---|---|---|
| HTTPS | Not set up | ACM cert + 443 listener; needs a domain |
| Custom domain | Not set up | Route 53 record pointing at the ALB |
| HA database | Single AZ | `multi_az = true` on the RDS resource |
| Auto-scaling | Single task | `aws_appautoscaling_target` + target-tracking policies on Fargate |
| Tenant isolation | Single tenant | Per-tenant DB schemas or DBs; per-tenant IAM scoping |
| WAF | Not set up | `aws_wafv2_web_acl` in front of the ALB |
| Backups | RDS retains 7 days | Cross-region snapshots; AWS Backup plan |
| Observability | CloudWatch logs only | CloudWatch metrics dashboards; X-Ray traces; alarms |
| Image scanning beyond ECR | Scan-on-push only | Inspector or third-party SCA |
| State backend | Local | S3 backend (uncomment in `main.tf`) once the state bucket exists |

Each of these is a clearly-scoped follow-up. The current stack is intentionally bare so you can read it end-to-end and decide what to harden first.

## Cost notes

Idle (no traffic):

| Resource | Approx. USD/month |
|---|---|
| RDS db.t4g.micro + 20 GB gp3 | ~18 |
| Application Load Balancer | ~22 |
| Fargate (1 task, 0.5 vCPU / 1 GB, 24×7) | ~10 |
| CloudWatch logs (light) + Secrets Manager | ~3 |
| **Total** | **~$53/month idle** |

Under load you mostly pay incremental Fargate hours, ALB LCUs, and Bedrock token usage. The Bedrock cost is invisible to this stack — it shows up on the AWS bill as Bedrock charges, separately from the infra here, and is tracked per-workflow by `CostReportService`.

## Tear-down

```bash
terraform destroy
```

Caveats:
- ECR images aren't auto-deleted; remove them first if the repo refuses to drop.
- The RDS `final snapshot` setting is off in this dev config — the database disappears.
- Empty the S3 bucket before destroy if it has objects (or add `force_destroy = true`).

## Re-applying when only the image changes

The `aws_ecs_service` has `lifecycle { ignore_changes = [task_definition] }` so `terraform apply` doesn't fight your `ecs update-service --force-new-deployment` rolls. Run those whenever you push a new image. Re-run `terraform apply` only when you change Terraform-managed config (instance class, env vars, secrets, etc.).
