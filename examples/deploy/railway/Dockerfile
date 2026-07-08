# Railway production image. Official Python images do not publish a 3.14t tag,
# so uv installs CPython's free-threaded build on a standard slim base.
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHON_GIL=0 \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Pin the published Pounce release. Override this build argument deliberately
# when validating a newer release candidate.
ARG POUNCE_VERSION=0.8.1
RUN uv venv --python 3.14t /opt/venv \
    && uv pip install --python /opt/venv/bin/python "bengal-pounce==${POUNCE_VERSION}"

RUN useradd --create-home --uid 10001 pounce
COPY --chown=pounce:pounce app.py start.sh /app/
RUN chmod 0555 /app/start.sh

USER pounce

EXPOSE 8000
CMD ["/app/start.sh"]
