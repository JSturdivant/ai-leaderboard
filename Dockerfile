# Modelfit: standard-library Python, no dependencies to install.
FROM python:3.12.7-slim

ENV PYTHONUNBUFFERED=1 \
    MODELFIT_MODE=cloud \
    MODELFIT_DB=/data/modelfit.db \
    MODELFIT_DATA_DIR=/data/market
WORKDIR /srv/app
COPY app/ /srv/app/
RUN useradd --system --uid 10001 modelfit && mkdir -p /data && chown modelfit /data
USER modelfit
VOLUME ["/data"]
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=4)"
CMD ["python3", "server.py", "--host", "0.0.0.0", "--port", "8787"]
