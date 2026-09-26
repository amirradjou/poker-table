# syntax=docker/dockerfile:1
# poker-table in a container: the viewer, and a live table playing in it.
#   docker compose up            then open http://localhost:8765
#   docker run --rm -p 8765:8765 poker-table          (the same, without compose)
#   docker run --rm poker-table play -n 200 --seats tag,maniac,station

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so editing the source does not re-download them.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
RUN useradd --create-home --uid 10001 player && mkdir /data && chown player:player /data
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER player
WORKDIR /data
VOLUME /data
EXPOSE 8765
ENTRYPOINT ["poker-table"]
# A table that keeps playing, watchable at a human pace; hands pile up in /data/live.jsonl.
CMD ["serve", "/data/live.jsonl", "--live", "--host", "0.0.0.0", "-n", "100000", \
     "--seats", "tag,rock,maniac,station,random,tag", "--pace", "0.8"]
