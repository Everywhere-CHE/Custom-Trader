# Ubuntu 服务器部署指南

把 CustomTrader 部署为一台 Ubuntu 服务器上 7×24 运行的服务
（监控告警/自动订阅/数据同步持续工作），并通过 nginx 对外提供 HTTPS 访问。

## 1. 安装

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv nodejs nginx git
# Node 18+（Ubuntu 22.04 自带版本较旧时，用 NodeSource）：
# curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install -y nodejs

git clone https://github.com/Everywhere-CHE/Custom-Trader.git
cd custom-trader
./install.sh          # 创建 backend/.venv、装依赖、构建前端
```

## 2. 配置

```bash
# 允许外部访问（默认只绑 127.0.0.1）
echo "HOST=0.0.0.0" >> backend/.env
```

数据源在应用内「设置」页配置：FUYAO API Key（期货/期权分时）、
CTP 前置与账号（期货交易）。配置落盘在 `data/` 目录（自动生成）。

## 3. 启动

```bash
./start.sh --prod     # 前台运行，验证 http://服务器IP:8000/api/health
```

## 4. systemd 常驻（推荐）

`/etc/systemd/system/custom-trader.service`：

```ini
[Unit]
Description=CustomTrader
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/custom-trader
ExecStart=/opt/custom-trader/backend/.venv/bin/python /opt/custom-trader/backend/run.py
Restart=always
RestartSec=5
User=youruser

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now custom-trader
journalctl -u custom-trader -f
```

## 5. nginx HTTPS 反向代理（唯一对外端口）

`/etc/nginx/sites-available/custom-trader`：

```nginx
server {
    listen 443 ssl;
    server_name _;
    ssl_certificate     /etc/nginx/ssl/custom-trader.crt;
    ssl_certificate_key /etc/nginx/ssl/custom-trader.key;

    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
server { listen 80; return 301 https://$host$request_uri; }
```

```bash
sudo ln -s /etc/nginx/sites-available/custom-trader /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## 6. 备份

整个 `data/` 目录即全部状态（SQLite 库 + 配置 + 告警日志）：

```bash
tar czf backup-$(date +%F).tar.gz data/
```
