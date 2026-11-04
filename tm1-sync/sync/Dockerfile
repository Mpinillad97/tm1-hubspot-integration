FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# We set WORKDIR to the repo component root (tm1-sync)
WORKDIR /app

# Install deps for this component
COPY sync/requirements.txt /app/sync/requirements.txt
RUN pip install --no-cache-dir -r /app/sync/requirements.txt

# Copy code needed by the sync job:
# - sync/ (this component)
# - db/   (shared internal module)
COPY sync/ /app/sync/
COPY db/ /app/db/

# Ensure Python can import /app/db as module "db"
ENV PYTHONPATH=/app

# Default command (DO Job can override, but this is sensible)
CMD ["python", "-m", "sync.main", "--once"]