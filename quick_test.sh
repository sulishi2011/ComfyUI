#!/bin/bash

# ComfyUI 多 GPU 快速测试脚本

set -e

echo "================================"
echo "ComfyUI 多 GPU 快速测试"
echo "================================"

# 认证 Token 设置
# 优先从环境变量读取，否则提示输入
if [ -z "$COMFY_AUTH_TOKEN" ]; then
    echo ""
    echo "请输入 ComfyUI 认证 Token（留空表示无需认证）："
    read -r AUTH_TOKEN
    echo ""
else
    AUTH_TOKEN="$COMFY_AUTH_TOKEN"
    echo "🔐 Using authentication token from environment"
fi

# 设置认证头
if [ -n "$AUTH_TOKEN" ]; then
    AUTH_HEADER="Authorization: Bearer $AUTH_TOKEN"
    echo "✓ Authentication enabled"
else
    AUTH_HEADER=""
    echo "⚠ Running without authentication"
fi
echo ""

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 检查函数
check_pass() {
    echo -e "${GREEN}✅ $1${NC}"
}

check_fail() {
    echo -e "${RED}❌ $1${NC}"
}

check_warn() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

# 1. 检查环境
echo "步骤 1: 检查环境"
echo "--------------------------------"

# 检查 Python
if command -v python &> /dev/null; then
    check_pass "Python 已安装"
    python --version
else
    check_fail "Python 未安装"
    exit 1
fi

# 检查 CUDA
if python -c "import torch; assert torch.cuda.is_available()" 2>/dev/null; then
    GPU_COUNT=$(python -c "import torch; print(torch.cuda.device_count())")
    check_pass "CUDA 可用，检测到 $GPU_COUNT 张 GPU"

    if [ "$GPU_COUNT" -ne 4 ]; then
        check_warn "GPU 数量不是 4，当前为 $GPU_COUNT"
        echo "是否继续测试？(y/n)"
        read -r answer
        if [ "$answer" != "y" ]; then
            exit 1
        fi
    fi
else
    check_fail "CUDA 不可用"
    exit 1
fi

echo ""

# 2. 检查配置文件
echo "步骤 2: 检查配置文件"
echo "--------------------------------"

if [ -f "start_multi_gpu.sh" ]; then
    check_pass "启动脚本存在"
else
    check_fail "start_multi_gpu.sh 不存在"
    exit 1
fi

if [ -f "nginx.conf" ]; then
    check_pass "Nginx 配置存在"
else
    check_warn "nginx.conf 不存在（可选）"
fi

echo ""

# 3. 检查 ComfyUI 是否运行
echo "步骤 3: 检查 ComfyUI 运行状态"
echo "--------------------------------"

# 构建 curl 命令
if [ -n "$AUTH_HEADER" ]; then
    CURL_CMD="curl -s -H \"$AUTH_HEADER\""
else
    CURL_CMD="curl -s"
fi

if eval $CURL_CMD http://localhost:8188 > /dev/null 2>&1; then
    check_pass "ComfyUI 正在运行 (端口 8188)"

    # 检查是否是多 GPU 模式
    if eval $CURL_CMD http://localhost:8188/queue/all | grep -q "queues"; then
        check_pass "多 GPU 模式已启用"

        # 显示队列状态
        echo ""
        echo "当前队列状态："
        eval $CURL_CMD http://localhost:8188/queue/all | python -m json.tool 2>/dev/null || echo "无法解析队列状态"
    else
        check_warn "似乎运行在单 GPU 模式"
        echo "提示：使用 ./start_multi_gpu.sh 启动多 GPU 模式"
    fi
else
    check_fail "ComfyUI 未运行"
    echo ""
    echo "请先启动 ComfyUI："
    echo "  ./start_multi_gpu.sh"
    exit 1
fi

echo ""

# 4. 测试基本 API
echo "步骤 4: 测试基本 API"
echo "--------------------------------"

# 测试 /queue 接口
if eval $CURL_CMD http://localhost:8188/queue > /dev/null; then
    check_pass "/queue 接口正常"
else
    check_fail "/queue 接口异常"
fi

# 测试 /queue/all 接口
if eval $CURL_CMD http://localhost:8188/queue/all > /dev/null; then
    check_pass "/queue/all 接口正常"
else
    check_fail "/queue/all 接口异常"
fi

# 测试 /history 接口
if eval $CURL_CMD http://localhost:8188/history > /dev/null; then
    check_pass "/history 接口正常"
else
    check_fail "/history 接口异常"
fi

echo ""

# 5. 检查 Nginx（如果配置了）
echo "步骤 5: 检查 Nginx 配置（可选）"
echo "--------------------------------"

NGINX_OK=false
for port in 8181 8182 8183 8184; do
    if eval $CURL_CMD http://localhost:$port > /dev/null 2>&1; then
        check_pass "端口 $port 可访问"
        NGINX_OK=true
    else
        check_warn "端口 $port 不可访问（Nginx 可能未配置）"
    fi
done

if [ "$NGINX_OK" = false ]; then
    echo ""
    echo "提示：如需使用 Nginx，请参考 TEST_GUIDE_4GPU.md"
fi

echo ""

# 6. 显示系统信息
echo "步骤 6: 系统信息"
echo "--------------------------------"

echo "GPU 信息："
nvidia-smi --query-gpu=index,name,memory.total,memory.used --format=csv,noheader 2>/dev/null || echo "无法获取 GPU 信息"

echo ""
echo "内存信息："
free -h | grep Mem || echo "无法获取内存信息"

echo ""
echo "ComfyUI 进程信息："
PID=$(pgrep -f "python.*main.py" | head -n 1)
if [ -n "$PID" ]; then
    echo "PID: $PID"
    RSS=$(ps -o rss= -p $PID)
    echo "内存占用: $((RSS / 1024))MB"
else
    echo "未找到 ComfyUI 进程"
fi

echo ""

# 7. 总结
echo "================================"
echo "测试完成"
echo "================================"
echo ""

if [ "$GPU_COUNT" -eq 4 ] && eval $CURL_CMD http://localhost:8188/queue/all | grep -q "queues"; then
    check_pass "所有基础检查通过！"
    echo ""
    echo "建议下一步："
    echo "  1. 提交测试任务验证功能"
    echo "  2. 运行并发测试验证性能"
    echo "  3. 查看详细测试指南: cat TEST_GUIDE_4GPU.md"
    echo ""
    if [ -n "$AUTH_HEADER" ]; then
        echo "快速测试命令（含认证）："
        echo "  # 设置环境变量"
        echo "  export COMFY_AUTH_TOKEN='your-token-here'"
        echo ""
        echo "  # 查看队列状态"
        echo "  curl -H 'Authorization: Bearer \$COMFY_AUTH_TOKEN' http://localhost:8188/queue/all | jq"
    else
        echo "快速测试命令："
        echo "  # 查看队列状态"
        echo "  curl http://localhost:8188/queue/all | jq"
    fi
    echo ""
    echo "  # 监控 GPU"
    echo "  watch -n 1 nvidia-smi"
    echo ""
    echo "  # 查看日志"
    echo "  tail -f logs/comfyui_*.log"
else
    check_warn "部分检查未通过，请查看上面的详细信息"
fi

echo ""
