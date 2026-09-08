#!/usr/bin/env python3
"""
CDK app entry point.

Single AWS account, dev + prod distinguished by stack naming
(ImageTextExtraction-Dev-* / -Prod-*) rather than separate accounts — see
docs/decisions.md for the reasoning. Each environment gets its own instance
of all three stacks; nothing is shared between dev and prod at the
infrastructure level.
"""

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

from stacks.data_stack import DataStack
from stacks.compute_stack import ComputeStack
from stacks.frontend_stack import FrontendStack

app = cdk.App()

# Runs the AWS Solutions rule pack against every stack on every synth (so
# locally and in the PR workflow's `cdk synth` step, not just at deploy
# time). Findings show up as CDK synth warnings/errors; anything
# deliberately not fixed gets a NagSuppressions entry with a reason, not
# silently ignored.
cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))

ENVIRONMENTS = ["dev", "prod"]

for env_name in ENVIRONMENTS:
    prefix = f"ImageTextExtraction-{env_name.capitalize()}"

    data_stack = DataStack(
        app,
        f"{prefix}-Data",
        env_name=env_name,
    )

    compute_stack = ComputeStack(
        app,
        f"{prefix}-Compute",
        env_name=env_name,
        upload_bucket=data_stack.upload_bucket,
        results_table=data_stack.results_table,
    )

    frontend_stack = FrontendStack(
        app,
        f"{prefix}-Frontend",
        env_name=env_name,
        api_url=compute_stack.api_url,
        access_log_bucket=data_stack.access_log_bucket,
    )

app.synth()
