FROM python:3.11-slim

WORKDIR /app

# Ensure the dashboard package is importable (Streamlit adds app.py's dir to sys.path,
# not WORKDIR — PYTHONPATH=/app makes 'import dashboard.queries' resolve correctly)
ENV PYTHONPATH=/app

# Install dashboard dependencies
RUN pip install --no-cache-dir \
    "streamlit>=1.35.0" \
    "psycopg2-binary==2.9.9" \
    "pandas==2.2.2" \
    "structlog>=24.1.0"

# Copy dashboard package
COPY dashboard/ dashboard/

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

CMD ["streamlit", "run", "dashboard/app.py", "--server.port", "8501", "--server.address", "0.0.0.0", "--server.runOnSave", "true"]
