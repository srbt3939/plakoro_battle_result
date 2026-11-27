"""Pure-Python DynamoDB low-level AttributeValue serializer / deserializer.

Works identically with ``boto3.client("dynamodb")`` and in-memory test doubles
without requiring ``boto3`` to be installed during offline unit tests.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def to_attr(value: Any) -> dict[str, Any]:
    """Serialize a Python value into a DynamoDB AttributeValue dict."""
    if value is None or value == "":
        raise ValueError("NULL and empty string attributes must be omitted in DynamoDB items.")
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, (int, Decimal)):
        return {"N": str(value)}
    if isinstance(value, float):
        return {"N": str(Decimal(str(value)))}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, (list, tuple)):
        return {"L": [to_attr(item) for item in value]}
    if isinstance(value, dict):
        return {
            "M": {
                str(k): to_attr(v)
                for k, v in value.items()
                if v is not None and v != ""
            }
        }
    raise TypeError(f"Unsupported DynamoDB attribute type: {type(value)!r}")


def from_attr(attr: dict[str, Any]) -> Any:
    """Deserialize a DynamoDB AttributeValue dict into Python primitives."""
    if "S" in attr:
        return attr["S"]
    if "BOOL" in attr:
        return bool(attr["BOOL"])
    if "NULL" in attr:
        return None
    if "N" in attr:
        raw = attr["N"]
        if "." in raw:
            return float(raw)
        return int(raw)
    if "L" in attr:
        return [from_attr(item) for item in attr["L"]]
    if "M" in attr:
        return {k: from_attr(v) for k, v in attr["M"].items()}
    raise ValueError(f"Unsupported AttributeValue structure: {attr!r}")


def serialize_item(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Convert a Python dict to a DynamoDB item map, omitting None and ''."""
    result: dict[str, dict[str, Any]] = {}
    for key, value in item.items():
        if value is None or value == "":
            continue
        result[key] = to_attr(value)
    return result


def deserialize_item(item: dict[str, dict[str, Any]] | None) -> dict[str, Any] | None:
    """Convert a DynamoDB item map back into a plain Python dict."""
    if item is None:
        return None
    return {key: from_attr(attr) for key, attr in item.items()}
