#!/usr/bin/env bash
#
# 增量更新：代码已到 /opt/ops-agent，把它构建并上线
#   sudo bash deploy/update.sh                      # 已在服务器 git pull 过
#   sudo bash deploy/update.sh --from-tar /tmp/x.tar.gz   # 从本地上传的包解压覆盖
#   sudo bash deploy/update.sh --no-stop            # 构建期间不停服务（内存够才用）
#
# 与 deploy.sh 的区别：deploy.sh 是从零装机（装包、建用户、配 sudoers、首次入库），
# 这个只做「换代码 → 装依赖 → 重新构建 → 重启 → 验证」，不碰 .env / data / logs。
#
# 安全性：不改 .env、不删 data 与 logs、不覆盖 sudoers；前端产物失败会自动回滚上一版。
#
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/ops-agent}"
APP_USER="${APP_USER:-ops}"
PIP_MIRROR="${PIP_MIRROR:-https://mirrors.aliyun.com/pypi/simple/}"
NPM_MIRROR="${NPM_MIRROR:-https://registry.npmmirror.com}"
HEALTH_URL="http://127.0.0.1:8000/api/health"

MODE="git"
TARBALL=""
NO_STOP=""

while [ $# -gt 0 ]; do
  case "$1" in
    --from-tar) MODE="tar"; TARBALL="${2:-}"; shift 2 ;;
    --from-git) MODE="git"; shift ;;
    --no-stop)  NO_STOP="1"; shift ;;
    -h|--help)  sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "未知参数：$1（--help 看用法）"; exit 2 ;;
  esac
done

c_ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
c_warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
c_err()  { printf '\033[31m✗\033[0m %s\n' "$*"; }
c_step() { printf '\n\033[1m── %s ──\033[0m\n' "$*"; }

# ── 0. 前置检查 ─────────────────────────────────────────
c_step "0. 前置检查"
[ "$(id -u)" -eq 0 ] || { c_err "请用 sudo 运行"; exit 1; }
[ -d "$APP_DIR" ] || { c_err "找不到 $APP_DIR"; exit 1; }
[ -f "$APP_DIR/.env" ] || { c_err "缺少 $APP_DIR/.env —— 线上配置不能凭空生成，先确认目录对不对"; exit 1; }
id "$APP_USER" >/dev/null 2>&1 || { c_err "用户 $APP_USER 不存在，这台机器可能还没跑过 deploy.sh"; exit 1; }
[ -x "$APP_DIR/.venv/bin/python" ] || { c_err "缺虚拟环境 $APP_DIR/.venv"; exit 1; }

PREV_COMMIT="unknown"
if [ -d "$APP_DIR/.git" ]; then
  sudo -u "$APP_USER" git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
  PREV_COMMIT="$(sudo -u "$APP_USER" git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
fi
c_ok "目录 $APP_DIR · 用户 $APP_USER · 当前提交 $PREV_COMMIT"

# 回滚：只回前端产物 + 把服务拉起来，绝不删代码
rollback() {
  c_err "更新失败：$1"
  if [ -d "${APP_DIR}/web/dist.prev" ]; then
    rm -rf "${APP_DIR}/web/dist"
    mv "${APP_DIR}/web/dist.prev" "${APP_DIR}/web/dist"
    c_warn "已把前端产物回滚到上一版"
  fi
  systemctl start ops-agent 2>/dev/null || true
  [ "$PREV_COMMIT" != "unknown" ] && \
    c_warn "如需连后端代码一起回滚：cd $APP_DIR && sudo -u $APP_USER git reset --hard $PREV_COMMIT"
  exit 1
}

# ── 1. 换代码 ───────────────────────────────────────────
c_step "1. 代码"
if [ "$MODE" = "tar" ]; then
  [ -f "$TARBALL" ] || { c_err "找不到包：$TARBALL"; exit 1; }
  # 关键：解压时排除 .env / data / logs / .git —— 这些是线上状态，不能被本地包覆盖
  tar -xzf "$TARBALL" -C "$APP_DIR" --strip-components=1 \
      --exclude='.env' --exclude='data' --exclude='logs' --exclude='.git' 2>/dev/null \
    || { c_err "解压失败，检查包内是否有一层根目录"; exit 1; }
  chown -R "${APP_USER}:${APP_USER}" "$APP_DIR"
  c_ok "已从 $TARBALL 覆盖代码（.env / data / logs 未动）"
else
  if [ -d "$APP_DIR/.git" ]; then
    NEW_COMMIT="$(sudo -u "$APP_USER" git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    c_ok "使用仓库当前提交 $NEW_COMMIT"
    if [ "$NEW_COMMIT" = "$PREV_COMMIT" ]; then
      c_warn "提交没变 —— 如果你还没在服务器上 git pull，先 pull 再跑本脚本"
    fi
  else
    c_warn "$APP_DIR 不是 git 仓库，跳过版本检查"
  fi
fi

# 备份上一版前端产物（回滚用）
if [ -d "${APP_DIR}/web/dist" ]; then
  rm -rf "${APP_DIR}/web/dist.prev"
  cp -a "${APP_DIR}/web/dist" "${APP_DIR}/web/dist.prev"
  c_ok "已备份上一版 dist"
fi

# ── 2. 停服务（2G 机器上 npm build 与 uvicorn 抢内存会 OOM）──
if [ -z "$NO_STOP" ]; then
  c_step "2. 暂停服务（构建期间约 1–3 分钟不可用）"
  systemctl stop ops-agent 2>/dev/null || true
  c_ok "ops-agent 已停止"
else
  c_step "2. 跳过停服（--no-stop）"
fi

# ── 3. 后端依赖 ─────────────────────────────────────────
c_step "3. 后端依赖"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q -i "$PIP_MIRROR" -r "$APP_DIR/requirements.txt" \
  || rollback "pip install 失败（看上面的报错，常见是镜像或版本冲突）"
c_ok "requirements.txt 已装齐"

# ── 4. 前端依赖与构建 ───────────────────────────────────
c_step "4. 前端构建"
[ -d "$APP_DIR/web" ] || rollback "找不到 $APP_DIR/web"

# 本次新增了 vue-router / pinia / @vue-flow/*，
# node_modules 里没有它们时 vite build 会直接报 "Failed to resolve import"
PATH_SAVE="/usr/local/bin:/usr/bin:/bin:${PATH}"
sudo -u "$APP_USER" env PATH="$PATH_SAVE" bash -c \
  "cd '$APP_DIR/web' && npm install --no-audit --no-fund --registry='$NPM_MIRROR'" \
  || rollback "npm install 失败（网络或 node 版本问题）"

sudo -u "$APP_USER" env PATH="$PATH_SAVE" bash -c \
  "cd '$APP_DIR/web' && npm run build" \
  || rollback "vite build 失败（看上面的具体模块名）"

[ -f "$APP_DIR/web/dist/index.html" ] || rollback "构建没产出 dist/index.html"
DIST_MB=$(du -sm "$APP_DIR/web/dist" | cut -f1)
c_ok "构建完成，dist ${DIST_MB}MB"

# ── 5. Nginx 配置（本次拆了 snippet，必须同步）───────────
c_step "5. Nginx 配置"
if [ -f "$APP_DIR/deploy/nginx-ops-proxy.conf" ]; then
  mkdir -p /etc/nginx/snippets
  cp "$APP_DIR/deploy/nginx-ops-proxy.conf" /etc/nginx/snippets/ops-agent-proxy.conf
  cp "$APP_DIR/deploy/nginx-ops-agent-ratelimit.conf" /etc/nginx/conf.d/ops-agent-ratelimit.conf
  cp "$APP_DIR/deploy/nginx-ops-agent.conf" /etc/nginx/sites-available/ops-agent
  ln -sf /etc/nginx/sites-available/ops-agent /etc/nginx/sites-enabled/ops-agent
  if nginx -t >/dev/null 2>&1; then
    systemctl reload nginx
    c_ok "Nginx 已重载（审计接口限流已放宽到 180r/m）"
  else
    c_err "Nginx 配置有误，未重载（站点仍用旧配置运行）："
    nginx -t
  fi
else
  c_warn "包里没有 deploy/nginx-ops-proxy.conf，跳过 Nginx 更新"
fi

# ── 6. 启动与健康检查 ───────────────────────────────────
c_step "6. 启动与验证"
systemctl start ops-agent
OK=""
for i in $(seq 1 25); do
  if curl -fsS --max-time 2 "$HEALTH_URL" >/tmp/ops-health.json 2>/dev/null; then OK="1"; break; fi
  sleep 1
done
[ -n "$OK" ] || { journalctl -u ops-agent -n 30 --no-pager; rollback "后端 25 秒内没起来"; }

cat /tmp/ops-health.json; echo
grep -Eq '"memory_backend": *"redis"' /tmp/ops-health.json \
  && c_ok "会话存储：redis（跨进程可见，审批单能持久）" \
  || c_warn "会话存储不是 redis —— 检查 redis-server 是否在跑"

# 三个新接口的冒烟
curl -fsS --max-time 5 "http://127.0.0.1:8000/api/graph"   | grep -q '"source"' && c_ok "/api/graph 可用"   || c_warn "/api/graph 异常"
curl -fsS --max-time 5 "http://127.0.0.1:8000/api/audit?page=1&page_size=1" | grep -q '"total"' && c_ok "/api/audit 可用" || c_warn "/api/audit 异常"
curl -fsS --max-time 5 "http://127.0.0.1:8000/api/actions" | grep -q '"counts"' && c_ok "/api/actions 可用" || c_warn "/api/actions 异常"

# ── 7. 收尾 ─────────────────────────────────────────────
c_step "7. 完成"
IP="$(curl -fsS --max-time 4 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
cat <<EOF

  打开： http://${IP}/
  审计： http://${IP}/audit
  审批： http://${IP}/actions
  编排： http://${IP}/graph

  本地自检（绕过公网，确认 Nginx 也通）：
    curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/
    curl -s http://127.0.0.1/api/health

  看日志： journalctl -u ops-agent -f
EOF
c_ok "更新完成（原提交 $PREV_COMMIT）"
