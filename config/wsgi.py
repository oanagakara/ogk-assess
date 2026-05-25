import logging
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Boot OTel only when the collector endpoint is configured.
_OTEL_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
if _OTEL_ENDPOINT:
    from opentelemetry import trace
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    _resource = Resource.create({"service.name": "assessment"})

    # Traces
    _tracer_provider = TracerProvider(resource=_resource)
    _tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(_tracer_provider)

    # Logs bridge — ships Python logging records via OTLP → OTel Collector → Loki
    _logger_provider = LoggerProvider(resource=_resource)
    _logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(_logger_provider)
    logging.getLogger().addHandler(
        LoggingHandler(level=logging.INFO, logger_provider=_logger_provider)
    )

    DjangoInstrumentor().instrument()
    Psycopg2Instrumentor().instrument()

application = get_wsgi_application()
