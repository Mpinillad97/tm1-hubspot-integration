from dataclasses import dataclass
from typing import Optional


@dataclass
class PublishErrorInfo:
    retryable: bool
    status_code: Optional[int]
    message: str


def classify_http_status(status_code: Optional[int]) -> PublishErrorInfo:
    if status_code is None:
        return PublishErrorInfo(retryable=True, status_code=None, message="no_status_network_error")

    if status_code == 429:
        return PublishErrorInfo(retryable=True, status_code=status_code, message="rate_limited")

    if status_code in (500, 502, 503, 504):
        return PublishErrorInfo(retryable=True, status_code=status_code, message="server_error")

    if status_code in (401, 403):
        return PublishErrorInfo(retryable=False, status_code=status_code, message="auth_error")

    if status_code == 404:
        return PublishErrorInfo(retryable=False, status_code=status_code, message="not_found_schema_or_object")

    if 400 <= status_code < 500:
        return PublishErrorInfo(retryable=False, status_code=status_code, message="bad_request")

    return PublishErrorInfo(retryable=True, status_code=status_code, message="unknown_status")