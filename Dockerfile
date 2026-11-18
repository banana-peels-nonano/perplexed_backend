# PERPLEXED 2.0: the app image (API + the static frontend in web/).
# Built by deploy/compose.prod.yaml; no secrets or case content go into it.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# dependencies first, so code changes don't reinstall them
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY web ./web
COPY scripts ./scripts
COPY tests/fixtures ./tests/fixtures
COPY deploy/start.sh ./start.sh

# run as a normal user, not root
RUN useradd --create-home --uid 10001 perplexed && chown -R perplexed /app
USER perplexed

EXPOSE 8000
CMD ["sh", "./start.sh"]
