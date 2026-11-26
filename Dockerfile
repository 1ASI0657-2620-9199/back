FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt requirements-kafka.txt ./
COPY libraries/finanzen_common ./libraries/finanzen_common
RUN python -m pip install -r requirements-kafka.txt \
    && useradd --create-home --uid 10001 finanzen \
    && mkdir /data && chown finanzen:finanzen /data
COPY --chown=finanzen:finanzen app ./app
COPY --chown=finanzen:finanzen services ./services
COPY --chown=finanzen:finanzen scripts ./scripts
USER finanzen
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=4s --start-period=15s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

FROM runtime AS tests
USER root
COPY requirements-dev.txt ./
RUN python -m pip install -r requirements-dev.txt
COPY --chown=finanzen:finanzen tests ./tests
COPY --chown=finanzen:finanzen features ./features
USER finanzen
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
