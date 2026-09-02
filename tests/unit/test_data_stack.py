"""
Basic smoke test that DataStack synthesizes and creates the resources we
expect. More detailed assertions (encryption, removal policy per env, etc.)
get added as the stack fills in.
"""

import sys
from pathlib import Path

import aws_cdk as cdk
from aws_cdk.assertions import Template

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra"))

from stacks.data_stack import DataStack  # noqa: E402


def test_data_stack_creates_bucket_and_table():
    app = cdk.App()
    stack = DataStack(app, "TestData", env_name="dev")
    template = Template.from_stack(stack)

    # Two buckets by design: the upload bucket and its dedicated access-log
    # bucket (see the AccessLogBucket comment in data_stack.py for why a
    # log bucket doesn't log to itself).
    template.resource_count_is("AWS::S3::Bucket", 2)
    template.resource_count_is("AWS::DynamoDB::Table", 1)
