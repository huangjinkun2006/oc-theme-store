#!/usr/bin/env bash
# 在「七牛云云服务器 / 任意 Ubuntu/Debian VPS」上一键部署主题商店后端。
# 用法：
#   curl -fsSL https://raw.githubusercontent.com/huangjinkun2006/oc-theme-store/main/deploy/setup.sh | sudo bash
# 或本地： sudo bash setup.sh
#
# 做的事：
#   1. 安装 Docker + docker compose 插件
#   2. 拉取仓库到 /opt/oc-theme-store
#   3. 启动容器（数据持久化在 /var/lib/ocstore）
#   4. 提示后续：域名解析 + nginx + HTTPS(certbot)
set -euo pipefail

REPO="https://github.com/huangjinkun2006/oc-theme-store"
DIR="/opt/oc-theme-store"
DATA="/var/lib/ocstore"

echo "==> 检测系统"
if ! command -v docker >/dev/null 2>&1; then
  echo "==> 安装 Docker"
  curl -fsSL https://get.docker.com | bash
  systemctl enable --now docker
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "==> 安装 docker compose 插件"
  apt-get update -y && apt-get install -y docker-compose-plugin
fi

echo "==> 拉取仓库"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only
else
  git clone "$REPO" "$DIR"
fi

echo "==> 创建持久数据目录 $DATA"
mkdir -p "$DATA"
grep -q '^STORE_DATA=' "$DIR/.env" 2>/dev/null || echo "STORE_DATA=$DATA" > "$DIR/.env"

echo "==> 启动服务"
cd "$DIR"
docker compose pull
docker compose up -d

echo
echo "==> 健康检查"
sleep 3
curl -fsS http://127.0.0.1:8000/api/health && echo "  OK" || echo "  检查 docker compose logs -f"

echo
echo "==> 后续（二选一暴露到公网）："
echo "   A. 直接用服务器 IP：把域名 A 记录指向本机公网 IP，再用 deploy/nginx.conf 反代（见 README）。"
echo "   B. 七牛云 CDN：源站填本机 IP:8000 或反代后的 80 端口，开启 HTTPS。"
echo "   HTTPS：certbot --nginx -d 你的域名"
echo
echo "==> App 里填的服务器地址：https://你的域名  （不带结尾斜杠）"
