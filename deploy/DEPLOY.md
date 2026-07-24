# showdown-service 一期上线手册

目标形态：`playground.aihubmix.com/showdown/*` → GCP LB 路径反代 → Azure VM 单体
（webapp.py：页面 + API + worker）→ MySQL 持久化队列。**零新增域名**。

```
用户 → playground.aihubmix.com/showdown/*
        └─ HTTPS LB(aihubmix-playground-load-balancer) path rule
             └─ Internet NEG → Azure VM (Caddy:443 → webapp.py:7788)
                  ├─ MySQL（jobs 队列，重启不丢）
                  ├─ 模型调用 → aihubmix 公开 API（BYOK，用户 key 仅驻内存）
                  └─ 产物 → VM 本地盘（无公网直链；量大再开 GCS/Blob）
```

## 步骤

### 1. Azure VM

- 规格：**B4ms**（4C16G，~$120/月）起步；黑洞级重 shader 实测卡顿再换
  **NC4as_T4_v3**（+NVIDIA 驱动）
- Ubuntu 22.04/24.04，开 80/443 入站；**给 VM 配 DNS name label**（Caddy 签证书用）
- 跑 `deploy/azure-vm-setup.sh`，然后：
  - `/etc/showdown.env`：填 `SHOWDOWN_DB_URL`；线上 BYOK-only 形态 **不要配**
    `AIHUBMIX_API_KEY`（配了=开放服务器代付+operator 品牌开关）
  - `showdown.config.json`：一期把 `web.clerk_publishable_key` 置空（无账户模式，
    只贴 key）、`web.gcs_bucket` 置空（本地盘）
  - `/etc/caddy/Caddyfile` 填 VM FQDN → `systemctl enable --now showdown caddy`

### 2. MySQL

- Azure Database for MySQL 灵活服务器最低档（~$15/月）或装在同 VM（$0）
- 建库：`CREATE DATABASE showdown CHARACTER SET utf8mb4;` 建表由服务启动时自动完成
- `SHOWDOWN_DB_URL=mysql://user:pass@host:3306/showdown`；本地开发不配则自动退
  SQLite（episodes/web/jobs.db），行为一致

### 3. 内测（不占任何域名）

直接访问 `https://<vm-fqdn>/showdown/`（Caddy 直连），跑通再挂 LB。

### 4. GCP LB 反代（对外）

`AZURE_FQDN=<vm-fqdn> bash deploy/gcp-lb-showdown-route.sh`
— 在 playground 的 url-map 上加 `/showdown/*` path rule（Internet NEG + 独立
backend service，不开 CDN，静态站不受影响）。回滚一条命令，见脚本尾部输出。

## 上线检查单

- [ ] `https://playground.aihubmix.com/showdown/healthz` = ok
- [ ] 贴 key 提交一单最小任务（gemini-3.6-flash + 10s）全链路通过
- [ ] `systemctl restart showdown` 后：queued 手动任务标 failed 提示重提、
      running 标 interrupted、历史任务列表还在（MySQL 生效）
- [ ] 视频直贴 URL 403、页面内播放/下载 200（Referer 防盗链）
- [ ] 无 logo：线上任务片尾不出现品牌（BYOK 无特权即无 logo）
- [ ] 重 shader 场景（黑洞 prompt）录屏帧率可接受；不行换 GPU 规格

## 已知边界（一期接受）

- 单 worker 串行：并发提交排队，页面显示位置
- Clerk 账户模式关闭：二期进 playground 前端时同域恢复
- Remotion license：Automators 档 $100/月，量大后评估合成层 HTML 化归零
