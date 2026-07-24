#!/usr/bin/env bash
#
# showdown-service Azure VM 初始化（Ubuntu 22.04/24.04，B4ms 起步；重 shader 场景
# 实测不行再换 NC4as_T4_v3 并装 NVIDIA 驱动）。
#
# 用法（在 VM 上以 sudo 运行）：
#   sudo bash azure-vm-setup.sh
#
# 之后：
#   1. /opt/showdown/showdown.config.json 按需改（一期 BYOK-only：clerk_publishable_key
#      置空即无账户模式；gcs_bucket 置空即只存本地盘）
#   2. /etc/showdown.env 填 SHOWDOWN_DB_URL（MySQL）等
#   3. systemctl start showdown && systemctl enable showdown
#   4. Caddy 会对 VM 的公网 FQDN 自动签证书（LB 反代的后端要求 HTTPS）
set -euo pipefail

REPO="${SHOWDOWN_REPO:-https://github.com/aihubmix/model-showdown.git}"
APP_DIR=/opt/showdown

apt-get update
apt-get install -y python3 python3-pip git curl ffmpeg fonts-noto-cjk

# Node 20（record.mjs / Remotion 依赖）
if ! command -v node >/dev/null || [[ "$(node -v | cut -c2-3)" -lt 20 ]]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
npm i -g pnpm

# 应用
if [[ ! -d "$APP_DIR" ]]; then
  git clone "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"
pnpm install
npx playwright install --with-deps chromium
(cd video && pnpm install)
pip3 install pymysql   # 唯一 Python 三方依赖（仅 MySQL 模式）

# 环境变量（密钥不进仓库）
if [[ ! -f /etc/showdown.env ]]; then
  cat > /etc/showdown.env <<'ENV'
# SHOWDOWN_DB_URL=mysql://user:pass@host:3306/showdown
SHOWDOWN_BASE_PATH=/showdown
# SHOWDOWN_GCS_BUCKET=            # 一期留空=只存本地盘
# AIHUBMIX_API_KEY=               # 留空=BYOK-only（推荐线上形态，用户自带 key）
ENV
  chmod 600 /etc/showdown.env
fi

# systemd
cat > /etc/systemd/system/showdown.service <<UNIT
[Unit]
Description=model-showdown web service
After=network-online.target

[Service]
WorkingDirectory=$APP_DIR
EnvironmentFile=/etc/showdown.env
ExecStart=/usr/bin/python3 $APP_DIR/webapp.py --host 127.0.0.1 --port 7788
Restart=always
RestartSec=5
# Playwright/Remotion 需要的头部空间
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
UNIT

# Caddy：对公网 FQDN 自动 HTTPS，反代到本机服务（GCP LB 的 Internet NEG 走 443）
if ! command -v caddy >/dev/null; then
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update && apt-get install -y caddy
fi
FQDN=$(curl -s -H Metadata:true \
  "http://169.254.169.254/metadata/instance/compute/publicIpAddress?api-version=2021-02-01&format=text" \
  2>/dev/null || hostname -f)
cat > /etc/caddy/Caddyfile <<CADDY
# 把 <vm-fqdn> 换成 VM 的 DNS 名（Azure 门户给 VM 配 DNS name label 后形如
# xxx.eastasia.cloudapp.azure.com）；裸 IP 无法签证书
<vm-fqdn> {
    reverse_proxy 127.0.0.1:7788
}
CADDY

systemctl daemon-reload
echo "done. 下一步：编辑 /etc/showdown.env 与 /etc/caddy/Caddyfile(<vm-fqdn>)，然后："
echo "  systemctl enable --now showdown caddy"
