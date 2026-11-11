"""HTTP application."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import auth, system, work
from app.core.config import get_settings
from app.core.errors import AppError, error_body
from app.core.logging import configure_logging, request_id_var

settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(title="Universal AI Documentation Platform", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Organization-Id", "Idempotency-Key", "X-Request-Id"],
)
app.include_router(auth.router)
app.include_router(work.router)
app.include_router(system.router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    incoming = request.headers.get("x-request-id") or str(uuid.uuid4())
    token = request_id_var.set(incoming)
    try:
        response = await call_next(request)
        response.headers["X-Request-Id"] = incoming
        return response
    finally:
        request_id_var.reset(token)


@app.exception_handler(AppError)
async def app_error(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=error_body(exc.category, exc.message, retryable=exc.retryable))


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = []
    for item in exc.errors():
        loc = ".".join(str(part) for part in item.get("loc", []) if part != "body")
        if loc:
            fields.append(loc)
    message = "Invalid fields: " + ", ".join(sorted(set(fields))) if fields else "The request is invalid."
    return JSONResponse(status_code=422, content=error_body("validation_error", message))


@app.exception_handler(Exception)
async def unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
    logging.getLogger("app").exception("unhandled error", exc_info=exc)
    if settings.app_env == "test":
        raise exc
    return JSONResponse(
        status_code=500,
        content=error_body("job_execution_error", "The request could not be completed. Check the service logs for the error type."),
    )
