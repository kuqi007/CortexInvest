#!/bin/bash
# 监控服务完整安装脚本
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$DIR/.venv"

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== AI 投资系统 - 监控服务安装脚本 ===${NC}"
echo ""

# 1. 检查系统依赖
echo -e "${BLUE}[1/6] 检查系统依赖...${NC}"

# 检查 uv
if ! command -v uv &> /dev/null; then
    echo -e "${YELLOW}安装 uv (Python 包管理器)...${NC}"
    curl -LsSf https://astral.sh/uv/install.sh | sh
else
    echo -e "${GREEN}✓ uv 已安装${NC}"
fi

# 检查 npm
if ! command -v npm &> /dev/null; then
    echo -e "${RED}✗ npm 未安装，请先安装 Node.js${NC}"
    exit 1
else
    echo -e "${GREEN}✓ npm 已安装 ($(npm --version))${NC}"
fi
# 检查 terminal-notifier
if ! command -v terminal-notifier &> /dev/null; then
    echo -e "${YELLOW}安装 terminal-notifier...${NC}"
    brew install terminal-notifier
else
    echo -e "${GREEN}✓ terminal-notifier 已安装${NC}"
fi

echo ""

# 2. 创建虚拟环境
echo -e "${BLUE}[2/6] 创建 Python 虚拟环境...${NC}"
if [ ! -d "$VENV_DIR" ]; then
    cd "$DIR"
    uv venv .venv
    echo -e "${GREEN}✓ 虚拟环境已创建${NC}"
else
    echo -e "${GREEN}✓ 虚拟环境已存在${NC}"
fi

echo ""

# 3. 安装 Python 依赖
echo -e "${BLUE}[3/6] 安装 Python 依赖...${NC}"
cd "$DIR"
source .venv/bin/activate
uv pip install -e . > /tmp/install_py.log 2>&1
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Python 依赖安装完成${NC}"
else
    echo -e "${RED}✗ Python 依赖安装失败，查看 /tmp/install_py.log${NC}"
    exit 1
fi

# 安装额外依赖
uv pip install setuptools > /tmp/install_setuptools.log 2>&1

echo ""

# 4. 安装 Playwright 浏览器
echo -e "${BLUE}[4/6] 安装 Playwright 浏览器...${NC}"
playwright install chromium > /tmp/install_browser.log 2>&1
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Playwright 浏览器安装完成${NC}"
else
    echo -e "${YELLOW}⚠ Playwright 浏览器安装可能有问题，稍后可手动运行: playwright install chromium${NC}"
fi

echo ""

# 5. 安装前端依赖
echo -e "${BLUE}[5/6] 安装前端依赖...${NC}"
cd "$DIR/web"
npm install > /tmp/install_npm.log 2>&1
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 前端依赖安装完成${NC}"
else
    echo -e "${RED}✗ 前端依赖安装失败，查看 /tmp/install_npm.log${NC}"
    exit 1
fi

echo ""

# 6. 创建配置文件
echo -e "${BLUE}[6/6] 创建配置文件...${NC}"
cd "$DIR"
if [ ! -f .env ]; then
    cp .env.example .env
    echo -e "${GREEN}✓ .env 文件已创建${NC}"
    echo -e "${YELLOW}⚠ 请编辑 .env 文件填入你的 API 密钥${NC}"
else
    echo -e "${GREEN}✓ .env 文件已存在${NC}"
fi

# 修改 start_monitor.sh 使用虚拟环境的 python
sed -i '' 's|^poetry run python|uv run python|g' start_monitor.sh 2>/dev/null || true

echo ""
echo -e "${GREEN}=== 安装完成! ===${NC}"
echo ""
echo "使用方法:"
echo "  ./monitor start      # 启动所有监控服务"
echo "  ./monitor stop       # 停止所有服务"
echo "  ./monitor status     # 查看服务状态"
echo "  ./monitor restart    # 重启所有服务"
echo ""
echo "重要提示:"
echo "  1. 请编辑 .env 文件填入你的 Gemini API 密钥"
echo "  2. 访问 http://localhost:3120 查看监控面板"
echo "  3. 日志文件保存在 logs/ 目录"
echo ""
