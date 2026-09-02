"""
List-results Lambda — returns recent extraction records for the history
view. v1 has no auth, so this is a single shared list (most-recent-first),
not scoped per user.

Queries the ListIndex GSI (constant partition key, created_at sort key)
rather than scanning the base table — see DataStack for the table design
reasoning. A Scan would read every item in the table regardless of how
many are actually returned; this Query only reads the page requested.
"""
from boto3.dynamodb.conditions import Key

from common.dynamo import results_table
from common.responses import json_response

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def handler(event, context):
    query_params = event.get("queryStringParameters") or {}
    try:
        limit = min(int(query_params.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)
    except ValueError:
        return json_response(400, {"error": "limit must be an integer"})

    response = results_table().query(
        IndexName="ListIndex",
        KeyConditionExpression=Key("record_type").eq("extraction"),
        ScanIndexForward=False,  # most recent first
        Limit=limit,
    )

    return json_response(200, {"items": response.get("Items", [])})
