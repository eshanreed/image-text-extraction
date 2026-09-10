# Image Text Extraction

A serverless 3-tier web app that extracts text from uploaded images using
Amazon Textract, deployed on AWS with CDK and a GitHub Actions CI/CD
pipeline. See `docs/decisions.md` for the reasoning behind every
non-trivial choice, including the real bugs hit getting it deployed.

## Architecture at a glance

- **Presentation tier** — a plain HTML/CSS/JS static site, served from S3
  behind CloudFront.
- **Application tier** — API Gateway + Lambda. One function per operation
  (upload, extract, list, get), each with its own least-privilege IAM role.
- **Data tier** — an S3 bucket for uploaded images and a DynamoDB table for
  extraction results.

Infrastructure is defined in CDK (Python), split into three stacks by
lifecycle rather than by tier:

| Stack | Contains | Why isolated |
|---|---|---|
| `DataStack` | S3 upload bucket, DynamoDB table | Changes rarely; stricter removal policy so compute/frontend churn can't take out stored data |
| `ComputeStack` | Lambda functions, API Gateway, IAM roles | Changes constantly as app logic iterates; takes data-tier resources as props instead of owning them |
| `FrontendStack` | S3 static site bucket, CloudFront distribution | Independent deploy cadence from backend logic |

Dev and prod are two instances of the same three stacks in one AWS account,
distinguished by name (`ImageTextExtraction-Dev-*` / `-Prod-*`). Dev deploys
automatically on merge to `main`; prod requires manual approval.

## Repo layout

```
infra/          CDK app (Python)
  app.py        Entry point — instantiates dev + prod stacks
  stacks/       DataStack, ComputeStack, FrontendStack
lambda/         One directory per Lambda function
  upload/
  extract/
  list_results/
  get_result/
  common/       Shared helpers (e.g. DynamoDB item shaping)
frontend/       Static site (HTML/CSS/JS)
tests/unit/     CDK stack tests (aws_cdk.assertions) and Lambda unit tests
.github/workflows/
  pr.yml        Lint, Bandit, tests, cdk synth, cdk-nag — no deploy
  deploy.yml    cdk deploy to dev on merge to main, manual gate before prod
```

## Status

Built, deployed, and confirmed working end-to-end in a live dev
environment (real image upload → Textract extraction → result shown in the
UI). The PR and deploy GitHub Actions workflows are both live, authenticating
to AWS via OIDC with no stored credentials. See `docs/decisions.md` for the
reasoning behind what's here and what's deliberately out of scope for v1.

## Local setup

```bash
cd infra
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cdk synth
```
