"""Pytest fixtures for Spark unit tests."""

import pytest
from pyspark.sql import SparkSession


@pytest.fixture(scope="session")
def spark():
    """Session-scoped SparkSession for unit tests."""
    spark_session = (
        SparkSession.builder.master("local[2]")
        .appName("test-feature-engine")
        .config("spark.sql.shuffle.partitions", "2")
        .getOrCreate()
    )
    yield spark_session
    spark_session.stop()
