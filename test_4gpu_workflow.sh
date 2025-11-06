#!/bin/bash

# 测试脚本：向 4 个 GPU 端点并发提交相同的 workflow

set -e

# 认证 Token
if [ -z "$COMFY_AUTH_TOKEN" ]; then
    echo "请输入 ComfyUI 认证 Token（留空表示无需认证）："
    read -r AUTH_TOKEN
else
    AUTH_TOKEN="$COMFY_AUTH_TOKEN"
fi

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}================================${NC}"
echo -e "${BLUE}4 GPU 串行 Workflow 测试${NC}"
echo -e "${BLUE}================================${NC}"
echo ""
echo -e "${YELLOW}注意：每个 GPU 将使用不同的随机 seed 生成不同的图片${NC}"
echo -e "${YELLOW}每个 GPU 调用间隔 5 秒${NC}"
echo ""

# 读取 workflow JSON
WORKFLOW_FILE="test-lora-api.json"

if [ ! -f "$WORKFLOW_FILE" ]; then
    echo -e "${RED}错误: 找不到 $WORKFLOW_FILE${NC}"
    exit 1
fi

# 读取 workflow 并构建 prompt
WORKFLOW_JSON=$(cat "$WORKFLOW_FILE")

# 构建请求体（需要包装成 {"prompt": {...}, "client_id": "..."} 格式）
CLIENT_ID="test-$(date +%s)"

echo -e "${GREEN}正在向 4 个 GPU 端点提交 workflow...${NC}"
echo ""

# 存储 prompt_id 和 seed
declare -a PROMPT_IDS
declare -a SEEDS

# 向 4 个端口并发提交
for port in 8181 8182 8183 8184; do
    gpu_id=$((port - 8181))

    # 生成随机 seed（使用时间戳 + GPU ID + 随机数）
    RANDOM_SEED=$(( $(date +%s%N | cut -b1-13) + gpu_id * 1000 + RANDOM ))

    # 替换 JSON 中的 seed 值
    MODIFIED_WORKFLOW=$(echo "$WORKFLOW_JSON" | sed "s/\"seed\": [0-9]\\+/\"seed\": $RANDOM_SEED/g")

    # 构建请求体
    REQUEST_BODY=$(cat <<EOF
{
  "prompt": $MODIFIED_WORKFLOW,
  "client_id": "${CLIENT_ID}_gpu${gpu_id}"
}
EOF
)

    echo -e "${YELLOW}>>> GPU $gpu_id (端口 $port) - Seed: $RANDOM_SEED${NC}"

    # 发送请求
    if [ -n "$AUTH_TOKEN" ]; then
        response=$(curl -s -X POST \
            -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d "$REQUEST_BODY" \
            "http://localhost:$port/prompt")
    else
        response=$(curl -s -X POST \
            -H "Content-Type: application/json" \
            -d "$REQUEST_BODY" \
            "http://localhost:$port/prompt")
    fi

    # 解析 prompt_id
    prompt_id=$(echo "$response" | python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('prompt_id', 'ERROR'))" 2>/dev/null || echo "ERROR")

    if [ "$prompt_id" != "ERROR" ]; then
        echo -e "  ${GREEN}✅ 提交成功${NC}"
        echo -e "  Prompt ID: $prompt_id"
        PROMPT_IDS[$gpu_id]=$prompt_id
        SEEDS[$gpu_id]=$RANDOM_SEED
    else
        echo -e "  ${RED}❌ 提交失败${NC}"
        echo -e "  响应: $response"
        PROMPT_IDS[$gpu_id]=""
        SEEDS[$gpu_id]=""
    fi

    echo ""

    # 每个 GPU 调用之间间隔 5 秒（最后一个不需要等待）
    if [ $gpu_id -lt 3 ]; then
        echo -e "${BLUE}等待 5 秒后提交下一个 GPU...${NC}"
        sleep 5
        echo ""
    fi
done

echo -e "${BLUE}================================${NC}"
echo -e "${GREEN}所有任务已提交！${NC}"
echo -e "${BLUE}================================${NC}"
echo ""

# 显示摘要
echo "提交摘要："
for i in {0..3}; do
    port=$((8181 + i))
    if [ -n "${PROMPT_IDS[$i]}" ]; then
        echo -e "  GPU $i (端口 $port): ${GREEN}${PROMPT_IDS[$i]}${NC} [Seed: ${SEEDS[$i]}]"
    else
        echo -e "  GPU $i (端口 $port): ${RED}失败${NC}"
    fi
done

echo ""
echo -e "${YELLOW}监控命令：${NC}"
echo ""
echo "# 查看所有队列状态"
if [ -n "$AUTH_TOKEN" ]; then
    echo "curl -H 'Authorization: Bearer \$COMFY_AUTH_TOKEN' http://localhost:8188/queue/all | python -m json.tool"
else
    echo "curl http://localhost:8188/queue/all | python -m json.tool"
fi
echo ""
echo "# 查看单个 GPU 队列"
echo "curl http://localhost:8181/queue  # GPU 0"
echo "curl http://localhost:8182/queue  # GPU 1"
echo "curl http://localhost:8183/queue  # GPU 2"
echo "curl http://localhost:8184/queue  # GPU 3"
echo ""
echo "# 监控 GPU 使用情况"
echo "watch -n 1 nvidia-smi"
echo ""

# 可选：等待并检查结果
echo -e "${YELLOW}是否等待任务完成并检查结果? (y/n)${NC}"
read -r wait_answer

if [ "$wait_answer" = "y" ]; then
    echo ""
    echo -e "${GREEN}等待任务完成...${NC}"
    echo "按 Ctrl+C 可以随时退出"
    echo ""

    sleep 3

    # 轮询检查任务状态
    while true; do
        all_done=true

        for i in {0..3}; do
            if [ -n "${PROMPT_IDS[$i]}" ]; then
                port=$((8181 + i))

                # 检查队列状态
                if [ -n "$AUTH_TOKEN" ]; then
                    queue_status=$(curl -s -H "Authorization: Bearer $AUTH_TOKEN" "http://localhost:$port/queue")
                else
                    queue_status=$(curl -s "http://localhost:$port/queue")
                fi

                running=$(echo "$queue_status" | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data.get('queue_running', [])))" 2>/dev/null || echo "0")
                pending=$(echo "$queue_status" | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data.get('queue_pending', [])))" 2>/dev/null || echo "0")

                if [ "$running" != "0" ] || [ "$pending" != "0" ]; then
                    all_done=false
                fi

                echo -e "GPU $i: running=$running, pending=$pending"
            fi
        done

        if [ "$all_done" = true ]; then
            echo ""
            echo -e "${GREEN}✅ 所有任务已完成！${NC}"
            break
        fi

        echo "---"
        sleep 2
    done

    echo ""
    echo -e "${GREEN}查看生成的图片：${NC}"
    echo "ls -lht output/SD1.5_*.png | head -n 4"
fi

echo ""
echo -e "${BLUE}测试完成！${NC}"
