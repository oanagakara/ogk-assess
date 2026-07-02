"""OpenTelemetry SDK setup for the Assessment platform.

Called from AssessmentConfig.ready() — after django.setup() and
dictConfig(LOGGING) have both run — so OTel log handlers attach
correctly to all named loggers regardless of propagate setting.

All exporters read OTEL_EXPORTER_OTLP_ENDPOINT (and optional
OTEL_EXPORTER_OTLP_HEADERS) from the environment so no secrets
live in code.  The function is a no-op when the env var is absent,
making local dev safe with zero config.

Uses HTTP/protobuf exporters (proto.http) — compatible with Grafana Cloud
and any standard OTLP HTTP endpoint.  Also works with a local OTel Collector
on port 4318 (set OTEL_EXPORTER_OTLP_ENDPOINT=http://host:4318).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


_initialized = False


def setup_otel() -> None:
    global _initialized
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint or _initialized:
        return
    _initialized = True

    from opentelemetry import metrics, trace
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
    from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    try:
        from opentelemetry.sdk.logs import LoggerProvider
        from opentelemetry.sdk.logs.export import BatchLogRecordProcessor
    except ImportError:
        from opentelemetry.sdk._logs import LoggerProvider  # type: ignore[no-redef]
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor  # type: ignore[no-redef]
    # LoggingHandler moved out of opentelemetry-sdk (deprecated there) and now
    # lives in the instrumentation package.
    from opentelemetry.instrumentation.logging.handler import LoggingHandler
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({
        "service.name": os.environ.get("OTEL_SERVICE_NAME", "assessment"),
        "service.version": os.environ.get("RENDER_GIT_COMMIT", "dev")[:8],
        "deployment.environment": "production" if os.environ.get("RENDER") else "development",
    })

    # ── Traces ────────────────────────────────────────────────────────────────
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(),          # reads OTEL_EXPORTER_OTLP_ENDPOINT
            max_queue_size=2048,
            max_export_batch_size=512,
            export_timeout_millis=10_000,
        )
    )
    trace.set_tracer_provider(tracer_provider)

    # ── Metrics ───────────────────────────────────────────────────────────────
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(),
                export_interval_millis=30_000,
            )
        ],
    )
    metrics.set_meter_provider(meter_provider)

    # ── Logs ──────────────────────────────────────────────────────────────────
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter())
    )
    set_logger_provider(logger_provider)

    # Bridge Python's logging module into the OTel logs pipeline so every
    # logger.info/warning/error call is exported to Loki via the collector.
    otel_handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    logging.getLogger().addHandler(otel_handler)
    # Django's LOGGING config sets propagate=False on named loggers so records never
    # reach root. Attach directly to each so they still get exported via OTel.
    for _logger in logging.Logger.manager.loggerDict.values():
        if isinstance(_logger, logging.Logger) and not _logger.propagate:
            _logger.addHandler(otel_handler)

    # ── Auto-instrumentation ──────────────────────────────────────────────────
    DjangoInstrumentor().instrument()
    Psycopg2Instrumentor().instrument()
    RedisInstrumentor().instrument()
    # Injects otelTraceID / otelSpanID into every log record so Grafana can
    # correlate a Loki log line with the matching Tempo trace.
    LoggingInstrumentor().instrument(set_logging_format=True)

    logger.info("OpenTelemetry initialised → %s (service=%s)", endpoint,
                resource.attributes.get("service.name"))
