"""Optional OpenTelemetry tracing — zero hard dependency, off by default.

Enable with ``OTEL_ENABLED=1`` and an installed SDK
(``pip install opentelemetry-sdk opentelemetry-exporter-otlp``). When the SDK
is missing or the flag is unset, every helper here is a silent no-op, so the
code can ship instrumented without forcing the dependency on deployments
that do not want it.

Standard OTel env vars (``OTEL_EXPORTER_OTLP_ENDPOINT``, ``OTEL_SERVICE_NAME``)
are honoured by the SDK itself.
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

_trace_module: Optional[Any] = None
_enabled = False


def otel_requested() -> bool:
    return str(os.getenv("OTEL_ENABLED") or "").strip().lower() in {"1", "true", "yes"}


def otel_available() -> bool:
    return _enabled


def init_otel(service_name: str = "gagent") -> bool:
    """Initialise the tracer provider when requested and the SDK is installed.

    Returns True when tracing is active. Never raises — observability must
    not break startup.
    """
    global _trace_module, _enabled
    if not otel_requested():
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.info(
            "OTEL_ENABLED=1 but opentelemetry-sdk is not installed; tracing disabled"
        )
        return False

    try:
        resource = Resource.create(
            {"service.name": os.getenv("OTEL_SERVICE_NAME", service_name)}
        )
        provider = TracerProvider(resource=resource)
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        except ImportError:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            except ImportError:
                logger.warning(
                    "OTel SDK present but no OTLP exporter (grpc/http) installed; "
                    "spans will be recorded but not exported"
                )
        trace.set_tracer_provider(provider)
        _trace_module = trace
        _enabled = True
        logger.info("OpenTelemetry tracing enabled service=%s", resource.attributes.get("service.name"))
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("OpenTelemetry initialisation failed: %s", type(exc).__name__)
        return False


@contextlib.contextmanager
def otel_span(name: str, **attributes: Any) -> Iterator[None]:
    """Wrap a block in a span when tracing is active; no-op otherwise."""
    if not _enabled or _trace_module is None:
        yield
        return
    tracer = _trace_module.get_tracer("gagent")
    with tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            if value is None:
                continue
            try:
                span.set_attribute(key, value)
            except Exception:  # pragma: no cover
                pass
        yield
