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

Table design: (1) get one record by id is a direct GetItem on the base
table. (2) list recent records, most-recent-first is NOT a Scan (a Scan
reads every item regardless of how many you want) — there's a GSI
(`ListIndex`) with a constant partition key (`record_type`, always
"extraction", since v1 has no per-user partitioning) and `created_at` as
the sort key, so "list recent" is a Query.

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

Private S3 bucket behind CloudFront using Origin Access Control (OAC, the
modern replacement for the older OAI pattern) — CloudFront is the only
thing allowed to read the bucket directly, no public bucket policy at all.

The frontend learns the API's URL through a small `config.js` file
generated at deploy time (via CDK's `BucketDeployment`, alongside the
static `frontend/` files) that sets `window.API_BASE_URL` — not
hand-typed anywhere, since the URL doesn't exist until ComputeStack
deploys.

Cache invalidation: `BucketDeployment`'s `distribution` +
`distribution_paths` args make every deploy automatically invalidate the
whole CloudFront cache, so there's no stale-content problem during active
development.

Deliberately not doing for v1:
- **Tightening CORS to the CloudFront domain.** The API currently accepts
  requests from any origin (`allow_origins=["*"]`). Scoping it to the real
  CloudFront domain would need ComputeStack to depend on FrontendStack's
  domain, and FrontendStack already depends on ComputeStack's API URL —
  the same two-way cross-stack dependency cycle documented below for
  S3-to-Lambda wiring, just between different stacks.
- **A custom domain.** No domain purchased for this project; Route 53 +
  ACM is real complexity for no benefit on a portfolio piece. Using
  CloudFront's default `*.cloudfront.net` domain has one real consequence:
  TLS can't be pinned to a modern minimum version (that's only
  configurable with a custom domain + ACM certificate), so the
  distribution is stuck on CloudFront's default certificate behavior.
- **AWS WAF and geo-restrictions on the distribution.** WAF is a real
  ongoing cost, and there's no requirement to block specific countries —
  neither is justified for a static demo site with no auth or sensitive
  data behind it. Would reconsider WAF for a production workload.
- **SPA-style error-page rewiring** (e.g. 404 → index.html). This is a
  handful of static pages, not a client-side-routed app, so the default
  S3/CloudFront error behavior is fine as-is.

## Auth: none in v1

"View past results" could mean per-user history (needs auth) or one shared
history. v1 uses a single shared history with no login, keeping the
DynamoDB keying and API surface simple. Cognito-based per-user auth is a
documented stretch feature — same category as the async Textract path —
not part of v1.

## Upload/extraction flow: presigned S3 URL + EventBridge, not one endpoint

The straightforward-sounding design is one API call that takes the image
inline and does everything synchronously. Rejected: API Gateway has a
10MB payload ceiling, and sending binary image data through it means
base64-encoding first (~33% size overhead) — an image legitimately under
this project's 10MB scope limit could still get rejected at the API
Gateway layer once encoded.

What's actually built: the upload Lambda never touches image bytes. It
creates a DynamoDB record (`status: pending`) and hands back a presigned
S3 PUT URL; the browser uploads directly to S3, bypassing API Gateway
entirely for the file transfer. That upload triggers the extract Lambda
automatically (see below), which runs Textract and updates the record.
The frontend polls `GET /results/{id}` until the status is `done` or
`failed`.

**S3-to-Lambda wiring is routed through EventBridge, not a direct S3
notification — and this was a real bug caught while building, not just a
theoretical concern.** The standard approach, `bucket.add_event_notification()`,
adds the notification config as a child of the bucket construct — which
lives in DataStack. That config needs the extract Lambda's ARN, but the
extract Lambda lives in ComputeStack, which *already* depends on DataStack
for the bucket/table ARNs it grants permissions on. Two stacks each
needing something from the other is a dependency cycle, and `cdk synth`
refused to build it. Fixed by turning on S3's native EventBridge
integration instead (`event_bridge_enabled=True` on the bucket — a
bucket-level setting with zero reference to ComputeStack) and defining an
EventBridge Rule inside ComputeStack that matches "object created under
uploads/" and targets the extract function. Every cross-stack reference
now points one direction only (Compute reads from Data, never the
reverse), and it's arguably the better pattern regardless, since
EventBridge lets more consumers subscribe to the same event later without
touching the bucket at all.

## API type: HTTP API (API Gateway v2), not REST API

Cheaper and simpler. This project doesn't need REST API's extra machinery
— usage plans, API keys, request-validation models — since every request
is validated inside its own Lambda handler.

## IAM: per-function least privilege, not shared helper grants

Every Lambda has its own execution role, and every grant names the
specific action(s) that function actually calls (e.g. `dynamodb:PutItem`
only for the upload function, `dynamodb:Query` only for list_results)
rather than reaching for CDK's broader helper grants like
`grant_read_write_data`. The one necessarily-unscoped permission is
Textract's `DetectDocumentText` (`resources=["*"]`) — AWS doesn't support
resource-level permissions for that action at all, since it operates on
data passed inline in the request, not an ARN-addressable resource.

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

- **DataStack** (S3 upload bucket, DynamoDB table, shared access-log
  bucket): changes rarely, RETAINed in prod. Isolated so iterating on
  compute or frontend can never risk stored data.
- **ComputeStack** (Lambda functions, API Gateway, IAM roles, the
  EventBridge rule): changes constantly. Takes DataStack's resources as
  constructor props rather than owning them, so it can redeploy
  independently.
- **FrontendStack** (S3 static site bucket, CloudFront distribution):
  independent deploy cadence from backend logic — a copy or CSS change
  never needs to touch Lambda code, and vice versa. Also takes
  ComputeStack's API URL and DataStack's access-log bucket as props — both
  one-directional references (Frontend reads from the others, never the
  reverse), so neither creates a cycle the way the S3-to-Lambda wiring
  originally did.

## cdk-nag: wired into every synth, zero findings

`AwsSolutionsChecks` runs as a CDK Aspect on every `cdk synth` (app.py),
so it's part of local development, not just something bolted onto CI
later. Every finding got one of two treatments, never a blanket
suppression:

**Fixed** (real, cheap fixes): Lambda runtime bumped to the latest Python;
CloudWatch access logging added to the API Gateway stage, the S3 upload
bucket, the S3 site bucket, and the CloudFront distribution itself (all
sharing one small dedicated `AccessLogBucket` in DataStack, since a bucket
can't sensibly log to itself).

**Suppressed with a written reason** (`NagSuppressions`, never silent):
the `AWSLambdaBasicExecutionRole` managed policy (grants only CloudWatch
Logs for a function's own log group — a hand-written policy would be
identical, so replacing it is cosmetic); a handful of S3 IAM
action-family wildcards from CDK's own `grant_put`/`grant_read` helpers
and from CDK's built-in custom resources (auto-delete-objects,
BucketDeployment) — framework-generated, not hand-written; the DynamoDB
GSI's `/index/*` resource pattern (CDK's `Table.grant()` can't scope to
one named index); Textract's unavoidable `resources=["*"]`; no-auth-on-API
(restates the v1 auth decision above); and the CloudFront TLS/WAF/geo
findings (restate the no-custom-domain decision above).

cdk-nag is pinned to exactly `2.38.2` in `infra/requirements.txt` — the
latest release (3.0.2) throws a jsii runtime error against current
aws-cdk-lib versions.

## Open / not yet decided

- GitHub Actions workflows (PR checks, main-branch deploy with an approval
  gate) — not yet built.
- AWS OIDC setup for GitHub Actions to deploy without stored credentials —
  needs one-time AWS console work.
- No live AWS deployment has happened yet. Everything above is verified
  via `cdk synth` (cdk-nag included) and unit tests only.
