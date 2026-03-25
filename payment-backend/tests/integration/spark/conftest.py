"""Pytest fixtures for Spark integration tests."""

import pytest
from pyspark.sql import SparkSession


@pytest.fixture(scope="session")
def spark():
    """Session-scoped SparkSession for integration tests."""
    spark_session = (
        SparkSession.builder
        .master("local[2]")
        .appName("test-feature-engineering-integration")
        .config("spark.sql.shuffle.partitions", "2")
        .getOrCreate()
    )
    yield spark_session
    spark_session.stop()
