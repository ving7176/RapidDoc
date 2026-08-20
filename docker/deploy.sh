#!/usr/bin/env bash
# RapidDoc 服务端一键部署脚本（纯 CPU）
#
# 用法（在服务器上）：
#   ./deploy.sh                # 默认配置部署/更新
#   ./deploy.sh --force        # 无缓存重建镜像（模型网络异常或想彻底重装时用）
#
# 可用环境变量覆盖：
#   APP_DIR=/opt/rapiddoc  REPO_URL=https://github.com/ving7176/RapidDoc.git \
#   BRANCH=build/docker-optimization  API_PORT=8888  ./deploy.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/rapiddoc}"
REPO_URL="${REPO_URL:-https://github.com/ving7176/RapidDoc.git}"
BRANCH="${BRANCH:-build/docker-optimization}"
API_PORT="${API_PORT:-8888}"
FORCE_REBUILD=false
[[ "${1:-}" == "--force" ]] && FORCE_REBUILD=true

log() { echo -e "\033[1;32m[deploy]\033[0m $*"; }
die() { echo -e "\033[1;31m[deploy][ERROR]\033[0m $*" >&2; exit 1; }

# ---------- 0. 前置检查 ----------
command -v docker >/dev/null || die "未找到 docker，请先安装 Docker"
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  die "未找到 docker compose / docker-compose"
fi
log "使用 compose 命令: $COMPOSE"
command -v curl >/dev/null || die "未找到 curl"

# ---------- 1. 获取/更新代码 ----------
if [ -d "$APP_DIR/.git" ]; then
  log "更新已有代码: $APP_DIR（分支 $BRANCH）"
  cd "$APP_DIR"
  git fetch --all --prune
  git checkout "$BRANCH" 2>/dev/null || git checkout -B "$BRANCH" "origin/$BRANCH"
  git pull --ff-only origin "$BRANCH" || die "git pull 失败（可能本地有未提交改动），请先处理: cd $APP_DIR && git status"
else
  log "首次部署，克隆仓库到 $APP_DIR（分支 $BRANCH）"
  mkdir -p "$(dirname "$APP_DIR")"
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
  cd "$APP_DIR"
fi
log "当前代码版本: $(git rev-parse --short HEAD)"

# ---------- 2. 停旧服务 ----------
cd "$APP_DIR/docker"
if $COMPOSE -f docker-compose.yml ps >/dev/null 2>&1; then
  log "停止旧服务..."
  $COMPOSE -f docker-compose.yml down --remove-orphans || true
fi

# ---------- 3. 端口占用检查（down 之后再查，避免把旧容器自身算作占用） ----------
if lsof -iTCP:"$API_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  log "警告: 端口 $API_PORT 仍被占用（可能是旧容器未完全释放或其他进程），将尝试继续，若启动失败请排查"
fi

# ---------- 4. 构建镜像（首次构建会下载模型，耗时较长） ----------
if [ "$FORCE_REBUILD" = true ]; then
  log "构建镜像（--no-cache，耗时较长）..."
  $COMPOSE -f docker-compose.yml build --no-cache
else
  log "构建镜像..."
  $COMPOSE -f docker-compose.yml build
fi

# ---------- 5. 启动 ----------
log "启动服务（内存限制 10g / swap 30g，见 docker-compose.yml）..."
$COMPOSE -f docker-compose.yml up -d

# ---------- 6. 健康检查（模型加载 + 服务启动，最长等 10 分钟） ----------
log "等待服务就绪（最长 10 分钟）..."
for i in $(seq 1 60); do
  if curl -sf "http://localhost:$API_PORT/health" >/dev/null 2>&1; then
    log "✅ 服务已就绪: http://localhost:$API_PORT/health"
    $COMPOSE -f docker-compose.yml ps
    log "部署完成。API 文档: http://localhost:$API_PORT/docs"
    log "查看日志: cd $APP_DIR/docker && $COMPOSE -f docker-compose.yml logs -f rapid-doc-server"
    exit 0
  fi
  sleep 10
done
die "健康检查超时。请查看日志: cd $APP_DIR/docker && $COMPOSE -f docker-compose.yml logs rapid-doc-server"
