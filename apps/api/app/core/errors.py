"""Consistent application errors. Responses never include stack traces."""

from __future__ import annotations


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        category: str,
        message: str,
        *,
        retryable: bool = False,
        detail: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.category = category
        self.message = message
        self.retryable = retryable
        self.detail = detail or {}


def error_body(category: str, message: str, *, retryable: bool = False) -> dict:
    return {"error": {"category": category, "message": message, "retryable": retryable}}
