# Nginx 反向代理部署指南

## 1. 安装 Nginx

### macOS
```bash
brew install nginx
```

### Ubuntu / Debian
```bash
sudo apt update && sudo apt install -y nginx
```

### CentOS / RHEL
```bash
sudo yum install -y epel-release
sudo yum install -y nginx
```

---

## 2. 配置反向代理

创建配置文件：

```bash
# macOS (Homebrew)
nano /opt/homebrew/etc/nginx/servers/duo-live.conf

# Linux
sudo nano /etc/nginx/sites-available/duo-live.conf
```

写入以下内容：

```nginx
server {
    listen 80;
    server_name your-domain.com;  # 替换为你的域名或 IP

    # ── 前端 (Next.js on port 3000) ──────────────────────
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # ── 后端 API (FastAPI on port 8899) ──────────────────
    location /api/ {
        proxy_pass http://127.0.0.1:8899;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # ── WebSocket 支持 (用于实时数据) ────────────────────
    location /ws {
        proxy_pass http://127.0.0.1:8899;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }
}
```

---

## 3. 启用配置

### Linux
```bash
sudo ln -s /etc/nginx/sites-available/duo-live.conf /etc/nginx/sites-enabled/
sudo nginx -t          # 测试配置
sudo systemctl reload nginx
```

### macOS (Homebrew)
```bash
nginx -t               # 测试配置
brew services restart nginx
```

---

## 4. 启动应用

确保在项目目录下：

```bash
cd /path/to/duo-live

# 构建前端
cd web && npm run build && cd ..

# 用 PM2 启动
pm2 start ecosystem.config.js
pm2 save
```

---

## 5. HTTPS (可选，推荐)

使用 Let's Encrypt 免费证书：

```bash
# 安装 certbot
sudo apt install -y certbot python3-certbot-nginx   # Ubuntu
# 或
brew install certbot                                  # macOS

# 自动配置 HTTPS
sudo certbot --nginx -d your-domain.com

# 自动续期
sudo certbot renew --dry-run
```

---

## 6. 常用命令

| 操作 | 命令 |
|------|------|
| 测试配置 | `nginx -t` |
| 重载配置 | `sudo systemctl reload nginx` |
| 查看状态 | `sudo systemctl status nginx` |
| 查看日志 | `tail -f /var/log/nginx/error.log` |
| PM2 状态 | `pm2 list` |
| PM2 日志 | `pm2 logs` |

---

## 架构图

```
Client (Browser)
       │
       ▼
   Nginx (:80/443)
       │
       ├── /          → Next.js (:3000)   前端
       ├── /api/*     → FastAPI (:8899)   后端 API
       └── /ws        → FastAPI (:8899)   WebSocket
```
