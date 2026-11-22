# Everything (Python, uv, ffmpeg, Hindi text shaping, fonts) lives in this image.
# Nothing is installed on the host beyond Docker.
FROM python:3.12-slim-bookworm

# ffmpeg: video assembly (Phase 3)
# libraqm0 + libharfbuzz0b + libfribidi0 + libfreetype6: correct Devanagari shaping in Pillow
# fonts-noto-core: includes Noto Sans Devanagari
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg libraqm0 libharfbuzz0b libfribidi0 libfreetype6 fonts-noto-core make \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /usr/local/bin/

# The venv lives outside /app so the bind-mounted source never hides it.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Dependencies first (cached layer), then the project itself (editable install).
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-install-project
COPY src ./src
RUN uv sync --frozen

CMD ["channel", "--help"]
