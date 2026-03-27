FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY docker /app/docker
COPY sql /app/sql

RUN pip install --no-cache-dir .

ENTRYPOINT ["/app/docker/entrypoint.sh"]

