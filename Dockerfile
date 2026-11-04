# baton fact server.
#   docker build -t agent-baton .
#   docker run --rm -v baton-data:/data agent-baton baton member add alice --db /data/baton.db
#   docker run -d -p 8765:8765 -v baton-data:/data agent-baton
# Serves plain HTTP: put it behind a TLS proxy, or pass --tls-cert/--tls-key.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install . && rm -rf /src

RUN useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin baton \
    && mkdir -p /data && chown baton:baton /data

USER baton
WORKDIR /data
VOLUME /data
EXPOSE 8765

# No HEALTHCHECK: every route, /v1/health included, needs a member token.
CMD ["baton", "serve", "--db", "/data/baton.db", "--host", "0.0.0.0"]
