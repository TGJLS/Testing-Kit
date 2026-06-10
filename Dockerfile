# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

# uv settings: copy instead of hardlink (no shared cache across layers) and
# compile bytecode for faster startup.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first in their own layer so they are cached across
# source-only changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Install the project itself.
COPY run.py README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Put the venv on PATH so the console script is directly invokable.
ENV PATH="/app/.venv/bin:$PATH"

# config.yaml / tasks.yaml are supplied at runtime (mount them into /app or
# pass -c/-t paths). Flags are forwarded to the runner, e.g.:
#   docker run -v "$PWD:/app" IMAGE -c config.yaml -t tasks.yaml
ENTRYPOINT ["adaptix-testing"]
