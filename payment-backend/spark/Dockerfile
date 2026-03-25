FROM python:3.11-slim

# Install OpenJDK 17 (required by Spark/PySpark)
RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-21-jre-headless \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
ENV PYTHONPATH=/app

# Install Python dependencies (PySpark bundles Spark binaries)
RUN pip install --no-cache-dir \
    pyspark==3.5.1 \
    redis==5.0.4 \
    structlog \
    pydantic>=2.7.0

# Copy spark module into container
COPY spark/ /app/spark/

WORKDIR /app

# spark-submit with Kafka connector JAR
# pip-installed pyspark places spark-submit at /usr/local/bin/spark-submit
CMD ["spark-submit", \
     "--master", "local[*]", \
     "--packages", "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.1", \
     "/app/spark/feature_engineering.py"]
