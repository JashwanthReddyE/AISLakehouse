# Ingestion service image — built & test-runnable now, deployed to Container Apps in Week 4.
FROM python:3.11-slim

WORKDIR /app

# Install only runtime deps for a lean image.
COPY pyproject.toml ./
RUN pip install --no-cache-dir websockets>=12.0 azure-eventhub>=5.11

COPY ingestion/ ./ingestion/

# Always-on consumer. Container Apps runs this with min replicas = 1.
CMD ["python", "-m", "ingestion.main"]
