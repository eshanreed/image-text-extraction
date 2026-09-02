"""
Get-result Lambda — returns a single extraction record by id (including
the full extracted text), for the detail view.
"""
from common.dynamo import results_table
from common.responses import json_response


def handler(event, context):
    record_id = (event.get("pathParameters") or {}).get("id")
    if not record_id:
        return json_response(400, {"error": "missing id"})

    response = results_table().get_item(Key={"id": record_id})
    item = response.get("Item")

    if item is None:
        return json_response(404, {"error": "not found"})

    return json_response(200, item)
