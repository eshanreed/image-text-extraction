"""Shared helper for getting a handle to the results table."""
import os

import boto3

_dynamodb = boto3.resource("dynamodb")


def results_table():
    return _dynamodb.Table(os.environ["RESULTS_TABLE_NAME"])
