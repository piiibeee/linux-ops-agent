#!/usr/bin/env bash
# Linux Ops Agent 生产部署脚本（Ubuntu 22.04 / 24.04）
# 用法：sudo bash /opt/ops-agent/deploy/deploy.sh
# 幂等：可以反复执行，用于首次部署和后续更新

set -euo pipefail

APP_DIR=/opt/ops-agent
APP_USER=ops
REPO="${REPO:-https://github.com/piiibeee/linux-ops-agent.git}"
PIP_MIRROR="https://mirrors.huaweicloud.com/repository/pypi/simple"
NPM_MIRROR="https://registry.npmmirror.com"

c_ok()   { printf '\033[32m[ OK ]\033[0m %s\n' "$*"; }
c_info() { printf '\033[36m[ .. ]\033[0m %s\n' "$*"; }
c_warn() { printf '\033[33m[WARN]\033[0m %s\n' "$*"; }
c_err()  { printf '\033[31m[FAIL]\033[0m %s\n' "$*" >&2; }

[ "$(id -u)" -eq 0 ] || { c_err "请用 sudo 运行"; exit 1; }

# ── 0. 系统版本检查 ──────────────────────────────────────
. /etc/os-release
c_info "系统：${PRETTY_NAME}"
case "${VERSION_ID}" in
  22.04|24.04) : ;;
  *) c_warn "未在 22.04/24.04 上验证过，继续但可能有问题" ;;
esac

PY_VER=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo "none")
c_info "系统 Python：${PY_VER}（需要 ≥3.10）"

# ── 1. 系统依赖 ─────────────────────────────────────────
c_info "安装系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  python3 python3-venv python3-pip \
  nginx redis-server git curl ca-certificates \
  apache2-utils ufw \
  procps iproute2 iputils-ping dnsutils less
c_ok "系统依赖就绪"

# Node 版本不够就装 NodeSource 20
NEED_NODE=0
if ! command -v node >/dev/null 2>&1; then NEED_NODE=1; else
  NODE_MAJOR=$(node -v | sed 's/v\([0-9]*\).*/\1/')
  [ "${NODE_MAJOR}" -lt 18 ] && NEED_NODE=1
fi
if [ "${NEED_NODE}" -eq 1 ]; then
  c_info "安装 Node 20（前端构建需要 ≥18.18）"
  if curl -fsSL --max-time 30 https://deb.nodesource.com/setup_20.x 2>/dev/null | bash - >/dev/null 2>&1; then
    apt-get install -y -qq nodejs
  else
    c_warn "NodeSource 拉取失败（国内网络常见），改用系统源"
    apt-get install -y -qq nodejs npm
  fi
  NODE_MAJOR=$(node -v 2>/dev/null | sed 's/v\([0-9]*\).*/\1/' || echo 0)
  if [ "${NODE_MAJOR}" -lt 18 ]; then
    c_err "Node 版本 $(node -v 2>/dev/null || echo 无) 低于 18.18，前端构建会失败。"
    c_err "手动装：curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash - && sudo apt install -y nodejs"
    exit 1
  fi
fi
c_ok "Node $(node -v) / npm $(npm -v)"

# ── 2. 运行用户 ─────────────────────────────────────────
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd -r -m -s /bin/bash "${APP_USER}"
  c_ok "已创建用户 ${APP_USER}"
else
  c_info "用户 ${APP_USER} 已存在"
fi

# 关键：不加这两个组，Agent 读 journal 和 /var/log 会 Permission denied，
# 表现是工具返回「未能采集」，排查半天才发现是权限问题。
usermod -aG systemd-journal "${APP_USER}" 2>/dev/null || true
usermod -aG adm "${APP_USER}" 2>/dev/null || true
c_ok "已授权读取 journal 与 /var/log"

# ── 3. 代码 ─────────────────────────────────────────────
# 先把目录属主交给运行用户。踩过的坑：目录若由 root 手动 clone，
# 用 ops 执行 git 会报 "detected dubious ownership" 并直接退出。
mkdir -p "${APP_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}" 2>/dev/null || true

if [ -d "${APP_DIR}/.git" ]; then
  c_info "更新代码"
  # 显式声明安全目录，跨用户操作仓库时 git 才肯干活
  sudo -u "${APP_USER}" git config --global --add safe.directory "${APP_DIR}" 2>/dev/null || true
  sudo -u "${APP_USER}" git -C "${APP_DIR}" fetch --all -q
  # 跟随仓库当前分支，不硬编码 main（master 也能用）
  BRANCH=$(sudo -u "${APP_USER}" git -C "${APP_DIR}" rev-parse --abbrev-ref HEAD)
  sudo -u "${APP_USER}" git -C "${APP_DIR}" reset --hard "origin/${BRANCH}" -q
else
  c_info "克隆仓库到 ${APP_DIR}"
  sudo -u "${APP_USER}" git -c safe.directory="${APP_DIR}" clone -q "${REPO}" "${APP_DIR}"
fi
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
mkdir -p "${APP_DIR}/data/chroma"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/data"
c_ok "代码就位：$(sudo -u "${APP_USER}" git -C "${APP_DIR}" log -1 --format='%h %s' | cut -c1-60)"

# ── 4. Python 依赖 ──────────────────────────────────────
if [ ! -x "${APP_DIR}/.venv/bin/python" ]; then
  c_info "创建虚拟环境"
  sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/.venv"
fi
c_info "安装 Python 依赖（国内镜像，约 1-3 分钟）"
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install -q --upgrade pip -i "${PIP_MIRROR}"
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install -q -i "${PIP_MIRROR}" -r "${APP_DIR}/requirements.txt"
c_ok "Python 依赖就绪"

# ── 5. 环境变量 ─────────────────────────────────────────
if [ ! -f "${APP_DIR}/.env" ]; then
  cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
  sed -i "s|^CHROMA_DIR=.*|CHROMA_DIR=${APP_DIR}/data/chroma|" "${APP_DIR}/.env"
  sed -i "s|^REDIS_URL=.*|REDIS_URL=redis://127.0.0.1:6379/0|" "${APP_DIR}/.env"
  chown "${APP_USER}:${APP_USER}" "${APP_DIR}/.env"
  chmod 600 "${APP_DIR}/.env"
  c_warn "已生成 ${APP_DIR}/.env，但 Key 还是占位符。"
  cat <<EOF

────────────────────────────────────────────
下一步必须做：
  sudo nano ${APP_DIR}/.env
把 DASHSCOPE_API_KEY 换成真实 Key，保存后再执行一次本脚本。

顺带改这两项（生产环境建议值）：
  ALLOW_EXECUTE=true
  RECURSION_LIMIT=40
────────────────────────────────────────────
EOF
  exit 0
fi

if grep -q 'sk-xxxxxxxx' "${APP_DIR}/.env"; then
  c_err ".env 里的 DASHSCOPE_API_KEY 还是占位符，先改掉再跑"
  exit 1
fi
c_ok "环境变量已配置"

# ── 6. Redis ────────────────────────────────────────────
systemctl enable --now redis-server >/dev/null 2>&1 || true
if redis-cli ping >/dev/null 2>&1; then
  c_ok "Redis 运行中（$(redis-cli ping)）"
else
  c_err "Redis 没起来，检查 systemctl status redis-server"
  exit 1
fi

# ── 7. 知识库入库 ───────────────────────────────────────
c_info "构建向量库（首次约 1-2 分钟）"
sudo -u "${APP_USER}" bash -c "cd ${APP_DIR} && .venv/bin/python -m scripts.ingest" \
  && c_ok "向量库完成" \
  || c_warn "入库失败，稍后单独重跑：cd ${APP_DIR} && .venv/bin/python -m scripts.ingest"

# ── 8. 前端构建 ─────────────────────────────────────────
c_info "构建前端"
sudo -u "${APP_USER}" bash -c "cd ${APP_DIR}/web && npm install --silent --registry=${NPM_MIRROR} && npm run build --silent"
[ -f "${APP_DIR}/web/dist/index.html" ] || { c_err "前端构建产物缺失"; exit 1; }
c_ok "前端产物：$(du -sh "${APP_DIR}/web/dist" | cut -f1)"

# ── 9. systemd 服务 ─────────────────────────────────────
cp "${APP_DIR}/deploy/ops-agent.service" /etc/systemd/system/ops-agent.service
systemctl daemon-reload
systemctl enable ops-agent >/dev/null 2>&1
systemctl restart ops-agent
sleep 3
if systemctl is-active --quiet ops-agent; then
  c_ok "ops-agent 服务已启动"
else
  c_err "服务启动失败，看日志：journalctl -u ops-agent -n 50 --no-pager"
  exit 1
fi

# ── 10. sudoers（变更操作需要） ─────────────────────────
SUDOERS=/etc/sudoers.d/ops-agent
cat > "${SUDOERS}" <<EOF
# 只放行 journal 清理，参数限定为 --vacuum-size=<数字><单位>
${APP_USER} ALL=(root) NOPASSWD: /usr/bin/journalctl --vacuum-size=*
EOF
chmod 440 "${SUDOERS}"
if visudo -c -f "${SUDOERS}" >/dev/null 2>&1; then
  c_ok "sudoers 已授权：journalctl --vacuum-size=*"
else
  rm -f "${SUDOERS}"
  c_err "sudoers 语法错误，已回滚"
fi

# ── 11. Nginx ───────────────────────────────────────────
cp "${APP_DIR}/deploy/nginx-ops-agent-ratelimit.conf" /etc/nginx/conf.d/ops-agent-ratelimit.conf
cp "${APP_DIR}/deploy/nginx-ops-agent.conf" /etc/nginx/sites-available/ops-agent
ln -sf /etc/nginx/sites-available/ops-agent /etc/nginx/sites-enabled/ops-agent
rm -f /etc/nginx/sites-enabled/default
if nginx -t >/dev/null 2>&1; then
  systemctl reload nginx
  c_ok "Nginx 已生效"
else
  c_err "Nginx 配置有误："
  nginx -t
  exit 1
fi

# ── 12. 防火墙 ──────────────────────────────────────────
if command -v ufw >/dev/null 2>&1; then
  ufw allow 22/tcp >/dev/null 2>&1 || true
  ufw allow 80/tcp >/dev/null 2>&1 || true
  ufw allow 443/tcp >/dev/null 2>&1 || true
  ufw --force enable >/dev/null 2>&1 || true
  c_ok "防火墙：仅放行 22/80/443（8000 和 6379 不对外）"
fi

# ── 13. 自检 ────────────────────────────────────────────
echo
c_info "自检（后端加载向量库需要几秒，最多等 30 秒）"
HEALTH_OK=0
for i in $(seq 1 15); do
  if curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    HEALTH_OK=1; break
  fi
  sleep 2
done

if [ "${HEALTH_OK}" -eq 1 ]; then
  c_ok "后端健康检查通过"
  curl -s --max-time 3 http://127.0.0.1:8000/api/health; echo
else
  c_err "后端健康检查失败（等待 30 秒仍不可用）"
  echo "--- 最近日志 ---"
  journalctl -u ops-agent -n 40 --no-pager
  echo "--- 常见原因 ---"
  echo "1) .env 里 DASHSCOPE_API_KEY 无效 → 401"
  echo "2) 8000 端口被占 → ss -lntp | grep 8000"
  echo "3) 依赖缺失 → journalctl 里看 ImportError"
  exit 1
fi

if curl -fsS --max-time 5 http://127.0.0.1/ >/dev/null 2>&1; then
  c_ok "Nginx 静态站点可访问"
else
  c_warn "Nginx 返回异常，检查 /var/log/nginx/ops-agent.error.log"
fi

PUBLIC_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null \
         || curl -s --max-time 5 https://ifconfig.me 2>/dev/null \
         || echo "<服务器公网IP>")
echo
c_ok "部署完成"
cat <<EOF

访问地址：http://${PUBLIC_IP}/
健康检查：http://${PUBLIC_IP}/healthz
服务日志：journalctl -u ops-agent -f

注意：现在整个站点还没有访问口令，任何人拿到 IP 都能用。
在发给 HR 之前，先执行 DEPLOY.md 第 6 节「加访问口令」。
EOF
