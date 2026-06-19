"""Application Insights telemetry setup for the HODS API."""

import logging
import os

logger = logging.getLogger(__name__)


def configure_telemetry() -> None:
    conn_str = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
    if not conn_str:
        logger.info("Application Insights not configured — telemetry disabled.")
        return

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        configure_azure_monitor(connection_string=conn_str)
        logger.info("Application Insights telemetry enabled.")
    except ImportError:
        logger.warning(
            "azure-monitor-opentelemetry not installed — "
            "add it to requirements.txt to enable telemetry."
        )
