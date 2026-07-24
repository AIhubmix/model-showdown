#!/usr/bin/env bash
#
# 在 playground.aihubmix.com 的 HTTPS LB 上加 /showdown/* 反代 → Azure showdown 服务。
# 零新增域名/证书：复用 aihubmix-playground-load-balancer 的 url-map。
#
# 前置：Azure VM 已跑通 HTTPS（Caddy + DNS name label），把 FQDN 填到下面。
# 用法：AZURE_FQDN=xxx.eastasia.cloudapp.azure.com bash gcp-lb-showdown-route.sh
set -euo pipefail

AZURE_FQDN="${AZURE_FQDN:?set AZURE_FQDN=<azure vm fqdn>}"
URLMAP=aihubmix-playground-load-balancer
NEG=showdown-azure-neg
BACKEND=showdown-azure-backend

# 1. Internet NEG 指向 Azure 后端（FQDN:443）
gcloud compute network-endpoint-groups create "$NEG" \
  --network-endpoint-type=internet-fqdn-port --global 2>/dev/null || true
gcloud compute network-endpoint-groups update "$NEG" --global \
  --add-endpoint="fqdn=$AZURE_FQDN,port=443"

# 2. Backend service（动态内容：不开 CDN；自定义 Host 头指向 Azure 域名；
#    长一点的超时给媒体下载留余量）
gcloud compute backend-services create "$BACKEND" \
  --global --load-balancing-scheme=EXTERNAL_MANAGED \
  --protocol=HTTPS --timeout=120s 2>/dev/null || true
gcloud compute backend-services add-backend "$BACKEND" --global \
  --network-endpoint-group="$NEG" --global-network-endpoint-group 2>/dev/null || true
gcloud compute backend-services update "$BACKEND" --global \
  --custom-request-header="Host: $AZURE_FQDN"

# 3. url-map 加 path matcher：/showdown/* → showdown backend（静态站流量不受影响）
gcloud compute url-maps add-path-matcher "$URLMAP" \
  --path-matcher-name=showdown \
  --default-backend-bucket="$(gcloud compute url-maps describe "$URLMAP" \
      --format='value(defaultService)' | sed 's|.*/||')" \
  --backend-service-path-rules="/showdown/*=$BACKEND"

echo "done: https://playground.aihubmix.com/showdown/"
echo "回滚: gcloud compute url-maps remove-path-matcher $URLMAP --path-matcher-name=showdown"
