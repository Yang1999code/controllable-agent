.PHONY: help install test lint run api docker-build docker-up docker-down clean

help: ## 显示帮助信息
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## 安装所有依赖
	pip install -e ".[all]"

install-test: ## 安装测试依赖
	pip install -e ".[test]"

install-server: ## 安装服务器依赖
	pip install -e ".[server]"

test: ## 运行所有测试
	pytest tests/ -v --tb=short

test-cov: ## 运行测试并生成覆盖率报告
	pytest tests/ -v --cov=ai --cov=agent --cov=app --cov-report=term-missing

lint: ## 运行代码风格检查
	flake8 ai/ agent/ app/ --max-line-length=120 --ignore=E501,W503

run: ## 启动 CLI 交互模式
	python -m app.cli

run-legacy: ## 启动旧版 REPL 模式
	python -m app.cli --legacy

run-one-shot: ## 单次执行（使用方法: make run-one-shot MSG="你的问题"）
	python -m app.cli --one-shot "$(MSG)"

api: ## 启动 REST API 服务器
	python -m app.server

api-prod: ## 生产环境启动（多 worker）
	gunicorn app.server:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000

docker-build: ## 构建 Docker 镜像
	docker build -t controllable-agent .

docker-up: ## 使用 docker-compose 启动服务
	docker-compose up -d

docker-down: ## 停止 docker-compose 服务
	docker-compose down

docker-logs: ## 查看服务日志
	docker-compose logs -f

clean: ## 清理缓存文件
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".coverage" -exec rm -rf {} +
	find . -type d -name "htmlcov" -exec rm -rf {} +
	find . -type d -name "dist" -exec rm -rf {} +
