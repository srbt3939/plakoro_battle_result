"""Unified API error definitions and response helpers (手順7 §5 / 追加レビューB-7・D10)."""

from __future__ import annotations

import json
from typing import Any


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retry_after: int | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after = retry_after


def json_response(
    status_code: int,
    body: dict[str, Any],
    *,
    request_id: str,
    extra_headers: dict[str, str] | None = None,
    cookies: list[str] | None = None,
) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "private, no-store",
        "X-Request-Id": request_id,
    }
    if extra_headers:
        headers.update(extra_headers)
    resp: dict[str, Any] = {
        "statusCode": status_code,
        "headers": headers,
        "body": json.dumps(body, ensure_ascii=False),
    }
    if cookies:
        resp["cookies"] = cookies
    return resp


def redirect_response(
    location: str,
    *,
    request_id: str,
    cookies: list[str] | None = None,
) -> dict[str, Any]:
    resp: dict[str, Any] = {
        "statusCode": 302,
        "headers": {
            "Location": location,
            "Cache-Control": "private, no-store",
            "X-Request-Id": request_id,
        },
        "body": "",
    }
    if cookies:
        resp["cookies"] = cookies
    return resp


def error_response(err: ApiError, *, request_id: str) -> dict[str, Any]:
    err_obj: dict[str, Any] = {
        "code": err.code,
        "message": err.message,
        "requestId": request_id,
    }
    extra_headers: dict[str, str] = {}
    if err.retry_after is not None:
        err_obj["retryAfter"] = err.retry_after
        extra_headers["Retry-After"] = str(err.retry_after)
    return json_response(
        err.status_code,
        {"error": err_obj},
        request_id=request_id,
        extra_headers=extra_headers,
    )
