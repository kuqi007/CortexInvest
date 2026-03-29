#!/bin/bash
# 在新电脑上设置 ai-investor 项目
# 用法: ./setup_on_new_machine.sh

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
ONEDRIVE_DIR="$HOME/OneDrive - Autodesk/ai-investor-data"

echo "=== AI 投资系统 - 新电脑设置脚本 ==="
echo ""

# 1. 检查 OneDrive 目录
echo "[1/5] 检查 OneDrive 目录..."
if [ ! -d "$ONEDRIVE_DIR" ]; then
    echo "  错误: 找不到 OneDrive 目录: $ONEDRIVE_DIR"
    echo "  请先在 OneDrive 中创建 'ai-investor-data' 文件夹，并放入以下文件:"
    echo "  - monitor_config.json (持仓配置)"
    echo "  - alert_config.json (告警配置)"
    echo "  - trade_plans.json (交易计划)"
    echo "  - sim_trading.db (数据库，可为空)"
    echo ""
    read -p "  创建目录后按回车继续，或 Ctrl+C 退出: "
fi

if [ ! -d "$ONEDRIVE_DIR" ]; then
    echo "  错误: 目录仍不存在: $ONEDRIVE_DIR"
    exit 1
fi

# 检查是否有数据文件
if [ -z "$(ls -A "$ONEDRIVE_DIR" 2>/dev/null)" ]; then
    echo "  警告: OneDrive 目录为空!"
    echo "  请从其他电脑复制数据到: $ONEDRIVE_DIR"
    echo "  至少需要: monitor_config.json"
    echo ""
    read -p "  继续但跳过数据检查? (y/n): " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "  ✓ OneDrive 目录存在"

# 2. 检查/创建符号链接
echo ""
echo "[2/5] 设置数据目录符号链接..."
if [ -L "$DIR/src/data" ]; then
    CURRENT_TARGET=$(readlink "$DIR/src/data")
    if [ "$CURRENT_TARGET" = "$ONEDRIVE_DIR" ]; then
        echo "  ✓ 符号链接已正确配置"
    else
        echo "  符号链接指向其他位置: $CURRENT_TARGET"
        read -p "  是否重新创建? (y/n): " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm "$DIR/src/data"
            ln -s "$ONEDRIVE_DIR" "$DIR/src/data"
            echo "  ✓ 符号链接已更新"
        fi
    fi
elif [ -d "$DIR/src/data" ]; then
    echo "  警告: src/data 是普通目录，不是符号链接"
    echo "  移动现有目录到 OneDrive..."
    mv "$DIR/src/data" "$ONEDRIVE_DIR.bak.$(date +%s)"
    ln -s "$ONEDRIVE_DIR" "$DIR/src/data"
    echo "  ✓ 已创建符号链接"
else
    ln -s "$ONEDRIVE_DIR" "$DIR/src/data"
    echo "  ✓ 符号链接已创建"
fi

# 3. 安装 Python 依赖
echo ""
echo "[3/5] 安装 Python 依赖..."
cd "$DIR"
uv sync --all-extras
echo "  ✓ Python 依赖安装完成"

# 4. 安装前端依赖
echo ""
echo "[4/5] 安装前端依赖..."
cd "$DIR/web"
npm install
echo "  ✓ 前端依赖安装完成"

# 5. 检查 .env 文件
echo ""
echo "[5/5] 检查配置文件..."
cd "$DIR"
if [ ! -f .env ]; then
    cp .env.example .env
    echo "  ! 已创建 .env 文件，请编辑填入 API 密钥"
else
    echo "  ✓ .env 文件已存在"
fi

echo ""
echo "=== 设置完成! ==="
echo ""
echo "启动方法:"
echo "  ./start_ai_investor.sh start"
echo ""
echo "查看状态:"
echo "  ./start_ai_investor.sh status"
echo ""
