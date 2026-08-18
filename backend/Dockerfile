FROM python:3.12-slim

WORKDIR /app

# 国内云服务器上 ghcr.io 拉取 uv 镜像极慢，改走 PyPI 安装；
# UV_INDEX_URL 为空时等同于未设置，uv 会忽略。
ARG UV_INDEX_URL=
RUN if [ -n "$UV_INDEX_URL" ]; then pip install --no-cache-dir --index-url "$UV_INDEX_URL" uv; else pip install --no-cache-dir uv; fi

ENV UV_HTTP_TIMEOUT=900

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

COPY . .

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
