#!/bin/bash
# duo-live 一键部署脚本
# 用法: ssh到服务器后执行 ./deploy.sh

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "============================================"
echo "  🚀 Duo-Live 部署"
echo "============================================"
echo ""

# 1. 拉取最新代码
echo "📥 Step 1: 拉取最新代码..."
git pull origin main
echo ""

# 2. 安装 Python 依赖 (如有变动)
echo "📦 Step 2: 同步 Python 依赖..."
.venv/bin/pip install -r requirements.txt
echo ""

# 3. 构建前端 (standalone 模式)
echo "🔨 Step 3: 构建前端..."
cd web
rm -rf .next
npm install --frozen-lockfile 2>/dev/null || npm install
npm run build
# standalone 模式需要手动拷贝 static 资源
cp -r .next/static .next/standalone/.next/static
[ -d public ] && cp -r public .next/standalone/public
cd ..
echo ""

# 4. 重启所有服务
echo "♻️  Step 4: 重启 PM2 服务..."
pm2 restart ecosystem.config.js
echo ""

# 5. 确认状态
echo "✅ 部署完成!"
echo ""
pm2 status
echo ""
echo "📋 查看日志: pm2 logs"
echo "============================================"
