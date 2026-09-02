# Design decisions

Kept here so every choice below can be defended in an interview, not just
pointed at.

## CDK language: Python

Keeps infrastructure code and Lambda handlers in one language. Bandit
covers both for static analysis, so the PR pipeline needs one Python
linter/SAST tool instead of splitting across ecosystems.

Rejected: TypeScript. It's more common in CDK's own docs and examples, but
that's a minor stylistic edge, not worth a two-language codebase here.

## Database: DynamoDB

The data model is one entity (an extraction record: image reference,
extracted text, timestamp, status) accessed by key — get by id, list most
recent. No joins, no relational structure. DynamoDB fits that directly and
pairs with Lambda without a VPC or connection pooling.

Rejected: RDS. Would need a VPC and likely RDS Proxy to avoid connection
exhaustion from Lambda, real infrastructure the data model doesn't need. It
would make sense only to specifically demonstrate VPC networking, or if
the data actually had relational structure.

## Frontend: plain HTML/CSS/JS, no framework

The role being targeted (AWS ProServe, AIML) is judged on cloud
architecture, IaC, and CI/CD — not frontend framework choice. Plain
HTML/JS keeps the interview conversation on the parts of the project that
matter for that role, and skips a build step in the pipeline.

Rejected: React/similar. Legitimate if the goal were demonstrating
frontend skills, but that's not what this project is for.

## Frontend hosting: S3 + CloudFront

Standard static-site serverless pattern. Pairs with an API Gateway +
Lambda backend, no servers anywhere in the stack.

## Auth: none in v1

"View past results" could mean per-user history (needs auth) or one shared
history. v1 uses a single shared history with no login, keeping the
DynamoDB keying and API surface simple. Cognito-based per-user auth is a
documented stretch feature — same category as the async Textract path —
not part of v1.

## Environments: single AWS account, two environments by naming

Dev and prod are two instances of the same three stacks in one account,
distinguished by stack name (`ImageTextExtraction-Dev-*` /
`-Prod-*`). Dev deploys automatically on merge to `main`; prod requires a
manual approval gate.

Rejected: separate AWS accounts per environment. That's the pattern real
ProServe engagements typically use (account-level isolation via AWS
Organizations, per-account OIDC roles) and worth describing as "the next
step" in an interview, but it requires multiple actual AWS accounts and
more IAM/OIDC setup before anything can be deployed and tested solo.

No automatic dev teardown: every resource in this stack (Lambda, DynamoDB
on-demand, S3, CloudFront, API Gateway, Textract) is pay-per-use. There's
no idle compute (no EC2, no provisioned RDS) accruing cost while dev sits
unused, so scheduled teardown wouldn't be solving a real cost problem.

## CDK stack breakdown: by lifecycle, not by tier

Three stacks — `DataStack`, `ComputeStack`, `FrontendStack` — split by how
often each changes and what it would cost to lose, not by
presentation/application/data tier boundaries (though it happens to line
up closely here):

- **DataStack** (S3 upload bucket, DynamoDB table): changes rarely,
  RETAINed in prod. Isolated so iterating on compute or frontend can never
  risk stored data.
- **ComputeStack** (Lambda functions, API Gateway, IAM roles): changes
  constantly. Takes DataStack's resources as constructor props rather than
  owning them, so it can redeploy independently. Each Lambda gets its own
  IAM role scoped to exactly what it needs (e.g. the extract function gets
  `textract:DetectDocumentText` plus scoped DynamoDB/S3 access, not a
  shared "app role").
- **FrontendStack** (S3 static site bucket, CloudFront distribution):
  independent deploy cadence from backend logic — a copy or CSS change
  never needs to touch Lambda code, and vice versa.

## Open / not yet decided

- Exact Lambda handler granularity beyond the four listed (upload,
  extract, list_results, get_result) — may combine some as the API shape
  firms up.
- IAM policy specifics per function — being worked out as ComputeStack is
  built.
