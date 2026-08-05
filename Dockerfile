FROM python:3.12-slim

LABEL maintainer="Empire Code Team"
LABEL description="Controllable Agent - Multi-Agent Collaboration Framework"

# 设置工作目录
WORKDIR /app

# 环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY pyproject.toml .

# 安装 Python 依赖
RUN pip install --no-cache-dir \
    httpx>=0.28.0 \
    anthropic>=0.40.0 \
    pyyaml>=6.0 \
    jieba>=0.42 \
    prompt_toolkit>=3.0 \
    python-frontmatter>=1.1.0 \
    python-dotenv>=1.0.0 \
    fastapi>=0.100.0 \
    uvicorn>=0.23.0 \
    websockets>=12.0 \
    pytest>=8.0 \
    pytest-asyncio>=0.24

# 可选：安装 Playwright 用于浏览器自动化
# RUN pip install playwright>=1.50.0 && playwright install --with-deps chromium

# 复制项目代码
COPY ai/ ./ai/
COPY agent/ ./agent/
COPY app/ ./app/
COPY my_agent.py .

# 安装项目为可编辑模式
RUN pip install -e .

# 创建记忆目录
RUN mkdir -p /root/.agent-memory

# 默认命令
CMD ["python", "-m", "app.cli"]

# 暴露 API 端口（如果使用 REST API）
EXPOSE 8000
