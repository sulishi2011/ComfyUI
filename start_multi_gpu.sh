#!/bin/bash

# ComfyUI 多 GPU 启动脚本

set -e

echo "🚀 Starting ComfyUI Multi-GPU Mode"

# 环境变量
export CUDA_VISIBLE_DEVICES=0,1,2,3
export COMFY_MULTI_GPU_SCHED=1
export COMFY_NUM_GPUS=4

# CPU 线程限制（可选）
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4

# 可选：启用 CUDA 优化
export CUDA_LAUNCH_BLOCKING=0

# 日志目录
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/comfyui_$(date +%Y%m%d_%H%M%S).log"

# 启动
echo "📝 Log file: $LOG_FILE"
python main.py \
    --listen 0.0.0.0 \
    --port 8188 \
    2>&1 | tee "$LOG_FILE"
