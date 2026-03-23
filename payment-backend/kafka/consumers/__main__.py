"""Entrypoint for python -m kafka.consumers — Phase 02.

Docker CMD: ["python", "-m", "kafka.consumers"]
Python runs this __main__.py automatically when the package is invoked
as a module with -m.
"""

import os

import structlog

from kafka.consumers.validation_consumer import ValidationConsumer

logger = structlog.get_logger(__name__)


def main() -> None:
    bootstrap_servers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    logger.info("starting_validation_consumer", bootstrap_servers=bootstrap_servers)
    consumer = ValidationConsumer(bootstrap_servers=bootstrap_servers)
    consumer.run()


if __name__ == "__main__":
    main()
