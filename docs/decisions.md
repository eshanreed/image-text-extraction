# Design decisions

Why things are built the way they are — including the tradeoffs rejected
along the way and the real bugs hit getting this deployed, not just the
choices that ended up in the code.

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

This project is about cloud architecture, IaC, and CI/CD — not frontend
framework choice. Plain HTML/JS keeps the scope on the parts that actually
matter here, and skips a build step in the pipeline.

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

## CI/CD: GitHub Actions with OIDC, no stored AWS credentials

Two workflows (`.github/workflows/`):

- **`pr.yml`** — every PR into `main`: ruff, Bandit, pytest (24 tests), and
  `cdk synth` (which also runs cdk-nag, wired in as an Aspect in `app.py`).
  Nothing here touches real AWS — no credentials configured at all, since
  the stacks are environment-agnostic (no account/region pinned), so synth
  never needs to look anything up in a real account.
- **`deploy.yml`** — every push to `main`: deploys the dev stacks
  automatically, then the prod stacks, gated behind a GitHub Environment
  (`production`) with a required reviewer. That gate is entirely the
  environment's own configuration, not anything in the workflow file, so
  reviewers can change later without touching this repo.

Both jobs authenticate via GitHub's native OIDC federation
(`aws-actions/configure-aws-credentials`, `id-token: write` permission) —
a short-lived token is exchanged for temporary AWS credentials on each run,
so there are no long-lived access keys stored in the repo or in GitHub
secrets at all. See the IAM trust-policy debugging story below for the
part of this that didn't work on the first attempt.

## Real problems debugged getting this deployed

These happened during the first live deploy, not while writing the code —
worth remembering as concrete examples, since "read the error and fix it"
undersells what each of these actually took.

**OIDC trust-policy mismatch.** First deploy failed with a generic
`Not authorized to perform sts:AssumeRoleWithWebIdentity`. Every visible
piece of config — the trust policy JSON, the role ARN, the OIDC provider's
audience — looked correct. Diagnosed by adding a temporary debug step to
the workflow that decoded the actual OIDC token's claims and printed them,
which showed GitHub embedding immutable numeric repo/owner IDs in the
subject claim (a GitHub security-hardening behavior that kicks in after a
repo or account rename) that the trust policy hadn't accounted for. Fixed
by updating the trust policy to match, and proactively covered the
different subject-claim format GitHub uses for environment-gated jobs
(the `deploy-prod` job above) before it caused the same failure later.

**CloudFront access logging needs S3 ACLs.** The CloudFront distribution
failed to create with "the S3 bucket you specified for CloudFront logs
does not enable ACL access." Root cause: CloudFront's classic logging
feature writes an ACL grant directly onto its target bucket — there's no
bucket-policy alternative — and the shared `AccessLogBucket` had ACLs
disabled (S3's now-default `BUCKET_OWNER_ENFORCED`). Fixed by allowing
ACLs on just that one log-only bucket (`BUCKET_OWNER_PREFERRED`), keeping
every bucket holding real application data fully ACL-free. See the comment
on `AccessLogBucket` in `data_stack.py` for the full reasoning.

**Missing S3 CORS on the upload bucket.** The app uploads images via a
presigned URL — a direct browser-to-S3 PUT that never touches the API (see
"Upload/extraction flow" above). CORS had been configured on API Gateway
for the JSON endpoints, but that's a separate hop from the browser's PUT
straight to S3, which needs its own CORS config on the bucket itself.
Missing it surfaced as a generic browser "NetworkError" on every real
upload attempt — the browser refused to even send the request. Fixed by
adding a CORS rule on `UploadBucket`, scoped to just the `PUT` method the
app actually uses.

**Wrong event shape in the extraction Lambda.** The extract Lambda is
triggered by S3 via EventBridge (see "Upload/extraction flow" above for
why), but the handler was originally written expecting the event shape of
a *direct* S3-to-Lambda notification — a different, incompatible envelope
(no top-level `Records` array; EventBridge puts the bucket/object info
under `detail` instead). Every real invocation threw an immediate
`KeyError` before the code could even mark the record `failed`, so uploads
just sat in `pending` forever with no visible error outside that
function's own CloudWatch Logs. The bug passed every unit test because the
test itself built a synthetic event using the same wrong shape the handler
expected — code and test agreed with each other while both disagreed with
what AWS actually sends. Fixed both the handler and the test to match
EventBridge's real event envelope; see the module docstring in
`lambda/extract/handler.py` for the full event shape.

**New-account service restrictions.** Also hit and resolved along the way:
a newer AWS account defaults to a "Free Plan" tier that blocks
usage-based AI services like Textract until the account is explicitly
upgraded to the standard paid plan, and a related IAM/billing gap — a
non-root IAM user can't reach Billing console pages at all until root
explicitly flips an account-level "activate IAM access to billing" switch,
which no IAM policy can substitute for.

## Open / not yet decided

- Cognito-based per-user auth (see "Auth: none in v1" above) — documented
  stretch feature, not started.
- Async Textract (job + SNS/Step Functions, for documents over the current
  10MB/single-page scope) — documented stretch feature, not started.
- Separate AWS accounts per environment instead of name-based separation
  in one account (see "Environments" above) — worth describing as the
  production-grade next step, not built here.
