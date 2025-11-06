#!/bin/bash

# ComfyUI 多 GPU 启动脚本
# Usage: ./start_multi_gpu.sh
#    or: nohup ./start_multi_gpu.sh &

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

# 添加其他环境变量（根据需要取消注释）
# export COMFYUI_S3_BUCKET_NAME="your-bucket"
# export COMFYUI_AWS_ACCESS_KEY_ID="your-key"
# export COMFYUI_AWS_SECRET_ACCESS_KEY="your-secret"
# export COMFYUI_AWS_REGION="us-east-1"

# 日志目录
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/comfyui_$(date +%Y%m%d_%H%M%S).log"

# 启动
echo "📝 Log file: $LOG_FILE"
exec python3 main.py \
  --listen 0.0.0.0 --port 8188 --enable-cors-header \
  --disable-metadata \
  "$@" 2>&1 | tee -a "$LOG_FILE"
