"""
Shared fixtures for Lambda handler unit tests.

These are a different kind of test from tests/unit/test_*_stack.py: those
assert that CDK *renders the right shape of resources* (a bucket here, an
IAM policy there). These assert that the actual Python business logic
inside each handler does the right thing given a particular event and a
particular table/bucket state -- the part cdk-nag and the stack tests
can't see at all.

Handlers live at lambda/<name>/handler.py and import a sibling `common`
package (`from common.dynamo import results_table`). That only resolves
at runtime because CDK bundles the whole lambda/ directory together and
lambda/ itself becomes the working directory -- see
ComputeStack._make_function in infra/stacks/compute_stack.py. Locally we
get the same layout by putting lambda/ on sys.path here, once, for every
test under this directory.
"""
import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lambda"))

TABLE_NAME = "test-results-table"
BUCKET_NAME = "test-upload-bucket"
AWS_REGION = "us-east-1"

# Set at import time, as plain module-level statements, not inside a
# fixture. Every handler creates its boto3 client/resource at *its own*
# module import time (e.g. `_dynamodb = boto3.resource("dynamodb")` in
# common/dynamo.py), and pytest imports test modules -- which import
# handler modules -- during collection, before any fixture (even an
# autouse one) has had a chance to run. A fixture here would be one test
# too late; conftest.py itself importing first is what's actually early
# enough.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", AWS_REGION)
os.environ.setdefault("RESULTS_TABLE_NAME", TABLE_NAME)
os.environ.setdefault("UPLOAD_BUCKET_NAME", BUCKET_NAME)


@pytest.fixture
def results_table():
    """A moto-backed DynamoDB table with the same schema DataStack
    actually creates (base table keyed on `id`, plus the ListIndex GSI
    keyed on record_type/created_at) -- see infra/stacks/data_stack.py.
    Handler code talks to this exactly like the real table; nothing about
    table.put_item/query/update_item/get_item is mocked or stubbed, only
    the AWS API underneath it is, so a bug in a real key or expression
    would still fail these tests."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name=AWS_REGION)
        client.create_table(
            TableName=TABLE_NAME,
            AttributeDefinitions=[
                {"AttributeName": "id", "AttributeType": "S"},
                {"AttributeName": "record_type", "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
            ],
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "ListIndex",
                    "KeySchema": [
                        {"AttributeName": "record_type", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield boto3.resource("dynamodb", region_name=AWS_REGION).Table(TABLE_NAME)
