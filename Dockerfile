FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

# Create non-root user
RUN groupadd --system tracker && \
    useradd --system --gid tracker --create-home tracker

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY src/ src/

# Install the package
RUN pip install --no-cache-dir . && rm -rf /tmp/*

# Create directories for mounted volumes
RUN mkdir -p data logs obsidian && \
    chown -R tracker:tracker /app

USER tracker

CMD ["python", "-m", "tracker"]
