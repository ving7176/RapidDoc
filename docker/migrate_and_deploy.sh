#!/usr/bin/env bash
# RapidDoc 一键迁移部署：从官方的 hzkitty 源切换到自己的 ving7176 fork，并部署最新代码。
#
# 用法：bash migrate_and_deploy.sh
# 可选环境变量：APP_DIR（默认 /opt/rapiddoc）
#
# 脚本动作：
#   1. 定位代码目录（不存在则克隆 ving7176 fork）
#   2. 无论现有 origin 指向哪个源（hzkitty 等），强切到 ving7176 fork
#   3. fetch + checkout build/docker-optimization 分支 + pull 最新代码
#   4. docker-compose 停旧容器、构建新镜像（含模型下载）、启动、健康检查
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/rapiddoc}"
FORK_REPO="https://github.com/ving7176/RapidDoc.git"
BRANCH="build/docker-optimization"
API_PORT="${API_PORT:-8888}"

# compose 命令探测（二选一执行）
get_compose() {
  if docker compose version >/dev/null 2>&1; then echo "docker compose";
  elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose";
  else echo ""; fi
}

log() { echo -e "\033[1;32m[rapiddoc-deploy]\033[0m $*"; }
die()  { echo -e "\033[1;31m[rapiddoc-deploy][ERROR]\033[0m $*" >&2; exit 1; }

# ---------- 0. 前置检查 ----------
command -v docker >/dev/null || die "未找到 docker"
COMPOSE="$(get_compose)"
[ -n "$COMPOSE" ] || die "未找到 docker compose / docker-compose"
log "compose 命令: $COMPOSE"

# ---------- 1. 定位 / 克隆目录 ----------
if [ -d "$APP_DIR/.git" ]; then
  log "已有代码目录: $APP_DIR"
else
  mkdir -p "$APP_DIR"
  if [ -n "$(ls -A "$APP_DIR" 2>/dev/null)" ] && [ ! -d "$APP_DIR/.git" ]; then
    # 目录存在但非 git 仓库（例如之前只是 docker pull 过镜像、或手动建了目录）
    die "目录 $APP_DIR 存在但非 git 仓库（缺少 .git），且不为空，无法原地迁移。\n请先确认是否要清理该目录后重来：\n  rm -rf $APP_DIR   # 确认里面没有需要保留的配置文件后再执行\n然后重新运行本脚本。\n若该目录是空的，可直接忽略并重跑（脚本会自动使用它）。"
  fi
  log "无代码目录，克隆 ving7176 fork 到 $APP_DIR"
  git clone --branch "$BRANCH" "$FORK_REPO" "$APP_DIR"
fi
cd "$APP_DIR"

# ---------- 2. 确保 origin 指向 ving7176 fork ----------
CURR_ORIGIN="$(git remote get-url origin 2>/dev/null || echo '')"
if [ "$CURR_ORIGIN" != "$FORK_REPO" ] && [ "$CURR_ORIGIN" != "git@github.com:ving7176/RapidDoc.git" ]; then
  if [ -n "$CURR_ORIGIN" ]; then
    log "origin 当前是: $CURR_ORIGIN（非 ving7176），先移除再改为 fork"
    git remote remove origin
  fi
  log "设置 origin 为 ving7176 fork: $FORK_REPO"
  git remote add origin "$FORK_REPO"
  
  # 顺带把 hzkitty 官方仓库保留为 upstream（便于回看），幂等
  if ! git remote get-url upstream >/dev/null 2>&1; then
    git remote add upstream https://github.com/RapidAI/RapidDoc.git || true
  fi
else
  log "origin 已指向 ving7176 fork: $CURR_ORIGIN"
fi

# ---------- 3. 切分支 + 拉最新代码 ----------
log "fetch + checkout $BRANCH + pull"
git fetch --all --prune
git checkout "$BRANCH" 2>/dev/null || git checkout -B "$BRANCH" "origin/$BRANCH"
if [ -n "$(git status --porcelain)" ]; then
  log "检测到本地未提交改动，已暂存以备恢复（不覆盖）"
  git stash push -m "rapiddoc-auto-stash-$(date +%s)" || true
fi
git pull --ff-only origin "$BRANCH" || die "git pull 失败，请手动处理 $APP_DIR 的冲突"
log "当前代码版本: $(git rev-parse --short HEAD)"

# ---------- 4. 停旧容器 ----------
cd "$APP_DIR/docker"
if $COMPOSE -f docker-compose.yml ps >/dev/null 2>&1; then
  log "停止旧服务..."
  $COMPOSE -f docker-compose.yml down --remove-orphans || true
fi

# ---------- 5. 构建镜像（首次部署/换源后会重新下载模型，耗时较长） ----------
log "构建镜像（含模型下载，首次会重下模型，正常现象）..."
$COMPOSE -f docker-compose.yml build

# ---------- 6. 启动 ----------
log "启动服务（内存限制 10g / swap 30g）..."
$COMPOSE -f docker-compose.yml up -d

# ---------- 7. 健康检查（最长 10 分钟） ----------
log "等待服务就绪（最长 10 分钟）..."
for i in $(seq 1 60); do
  if curl -sf "http://localhost:$API_PORT/health" >/dev/null 2>&1; then
    log "✅ 服务已就绪: http://localhost:$API_PORT/health"
    $COMPOSE -f docker-compose.yml ps
    log "部署完成。查看日志: $COMPOSE -f docker-compose.yml logs -f rapid-doc-server"
    exit 0
  fi
  sleep 10
done
die "健康检查超时。查看日志: cd $APP_DIR/docker && $COMPOSE -f docker-compose.yml logs rapid-doc-server"
