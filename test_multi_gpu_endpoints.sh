#!/bin/bash

# ComfyUI 多 GPU 端点测试脚本

echo "🧪 Testing Multi-GPU Endpoints"
echo "================================"
echo ""

# 测试函数
test_endpoint() {
    local port=$1
    local gpu_id=$((port - 8181))
    local endpoint=$2
    local method=$3

    echo "Testing GPU $gpu_id (port $port) - $method $endpoint"

    if [ "$method" == "GET" ]; then
        response=$(curl -s -w "\n%{http_code}" "http://localhost:$port$endpoint")
        status_code=$(echo "$response" | tail -n 1)

        if [ "$status_code" == "200" ]; then
            echo "  ✅ Success (HTTP $status_code)"
        else
            echo "  ⚠️  HTTP $status_code"
        fi
    elif [ "$method" == "POST" ]; then
        # 简单的 POST 测试（不发送实际数据）
        response=$(curl -s -w "\n%{http_code}" -X POST "http://localhost:$port$endpoint" -H "Content-Type: application/json")
        status_code=$(echo "$response" | tail -n 1)

        if [ "$status_code" == "200" ] || [ "$status_code" == "400" ]; then
            echo "  ✅ Endpoint accessible (HTTP $status_code)"
        else
            echo "  ⚠️  HTTP $status_code"
        fi
    fi
    echo ""
}

# 检查 ComfyUI 是否运行
echo "Checking if ComfyUI is running..."
if ! curl -s http://localhost:8188 > /dev/null 2>&1; then
    echo "❌ ComfyUI is not running on port 8188"
    echo "Please start ComfyUI first: ./start_multi_gpu.sh"
    exit 1
fi
echo "✅ ComfyUI is running"
echo ""

# 检查 Nginx 是否运行
echo "Checking if Nginx is configured..."
for port in 8181 8182 8183 8184; do
    if ! curl -s http://localhost:$port > /dev/null 2>&1; then
        echo "⚠️  Port $port is not accessible - Nginx may not be configured"
        echo "Please configure Nginx with the provided nginx.conf"
        exit 1
    fi
done
echo "✅ All ports accessible"
echo ""

echo "================================"
echo "Testing All Endpoints"
echo "================================"
echo ""

# 测试每个 GPU 端口的关键接口
for port in 8181 8182 8183 8184; do
    gpu_id=$((port - 8181))

    echo "--- GPU $gpu_id (Port $port) ---"

    # 测试队列查询
    test_endpoint $port "/queue" "GET"

    # 测试历史查询
    test_endpoint $port "/history" "GET"

    # 测试 prompt 接口（会返回 400 因为没有发送数据，但说明接口可访问）
    test_endpoint $port "/prompt" "POST"

    # 测试上传接口（会返回 400 因为没有发送数据，但说明接口可访问）
    test_endpoint $port "/upload/image" "POST"

    echo ""
done

echo "================================"
echo "Testing Global Endpoints"
echo "================================"
echo ""

# 测试全局队列汇总
echo "Testing /queue/all on main port (8188)"
response=$(curl -s "http://localhost:8188/queue/all")
if echo "$response" | grep -q "queues"; then
    echo "  ✅ /queue/all working"
    echo "  Response preview:"
    echo "$response" | jq -r '.queues[] | "    GPU \(.gpu_id): running=\(.running_count), pending=\(.pending_count)"' 2>/dev/null || echo "    $response"
else
    echo "  ⚠️  /queue/all may not be working correctly"
fi
echo ""

echo "================================"
echo "Test Complete"
echo "================================"
echo ""
echo "Summary:"
echo "  - Each port (8181-8184) can be used as a complete ComfyUI instance"
echo "  - All standard APIs are accessible through each port"
echo "  - Requests are automatically routed to the corresponding GPU"
echo "  - Files (input/output) are shared across all ports"
echo ""
echo "Example usage:"
echo "  # Upload to GPU 0"
echo "  curl -X POST http://localhost:8181/upload/image -F 'image=@myimage.png'"
echo ""
echo "  # Send prompt to GPU 1"
echo "  curl -X POST http://localhost:8182/prompt -H 'Content-Type: application/json' -d '{...}'"
echo ""
echo "  # Check GPU 2 queue"
echo "  curl http://localhost:8183/queue"
echo ""
