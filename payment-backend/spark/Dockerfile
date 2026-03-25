FROM bitnami/spark:3.5

# Switch to root for pip install
USER root

# Install Python dependencies
RUN pip install --no-cache-dir \
    pyspark==3.5.8 \
    redis==5.0.4 \
    structlog \
    pydantic>=2.7.0

# Copy spark module into container
COPY spark/ /app/spark/

# Switch back to non-root user (bitnami default: 1001)
USER 1001

WORKDIR /app

# spark-submit with Kafka connector JAR
CMD ["spark-submit", \
     "--master", "local[*]", \
     "--packages", "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.8", \
     "/app/spark/feature_engineering.py"]
