#!/bin/bash
# ComfyUI 多 GPU 环境启动脚本
# 适配: Amazon Deep Learning OSS Nvidia Driver AMI GPU PyTorch 2.7 (Ubuntu 22.04)
# 虚拟环境: ~/py27
# ComfyUI: ~/workspace/ComfyUI
# Models: /opt/dlami/nvme/comfyui/models

set -euo pipefail

# ===== 彩色输出 =====
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'
log_i(){ echo -e "${GREEN}[INFO] $*${NC}"; }
log_w(){ echo -e "${YELLOW}[WARNING] $*${NC}"; }
log_e(){ echo -e "${RED}[ERROR] $*${NC}" >&2; }
log_s(){ echo -e "${BLUE}[SUCCESS] $*${NC}"; }

# ===== 路径与环境 =====
HOME_DIR="/home/ubuntu"
VENV_PATH="$HOME_DIR/py27"
COMFYUI_PATH="$HOME_DIR/workspace/ComfyUI"
MODELS_PATH="/opt/dlami/nvme/comfyui/models"
LOG_DIR="$COMFYUI_PATH/logs"
LOG_FILE="$LOG_DIR/comfyui_$(date +%Y%m%d_%H%M%S).log"
NGINX_CONF="$COMFYUI_PATH/nginx.conf"

# 缓存路径
export TORCH_HOME="$HOME_DIR/.cache/torch"
export HF_HOME="$HOME_DIR/.cache/huggingface"
export XDG_CACHE_HOME="$HOME_DIR/.cache"
export TRANSFORMERS_CACHE="$HF_HOME/transformers"

mkdir -p "$LOG_DIR" "$HOME_DIR/.cache"

# ===== 激活虚拟环境 =====
log_i "激活虚拟环境: $VENV_PATH"
if [ ! -f "$VENV_PATH/bin/activate" ]; then
  log_e "虚拟环境不存在: $VENV_PATH"
  exit 1
fi
source "$VENV_PATH/bin/activate"
log_s "虚拟环境已激活: $(which python3)"

# ===== 函数 =====
install_apt_if_needed(){
  log_i "检查系统依赖..."
  local missing_pkgs=()

  for pkg in lsof iproute2 psmisc ffmpeg nginx; do
    if ! dpkg -l | grep -q "^ii  $pkg "; then
      missing_pkgs+=("$pkg")
    fi
  done

  if [ ${#missing_pkgs[@]} -gt 0 ]; then
    log_i "安装缺失的系统包: ${missing_pkgs[*]}"
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends "${missing_pkgs[@]}" || log_w "部分系统包安装失败"
  else
    log_s "系统依赖已满足"
  fi
}

check_models_symlink(){
  log_i "检查 models 目录软链接..."

  local local_models="$COMFYUI_PATH/models"

  # 检查软链接是否存在且指向正确
  if [ -L "$local_models" ]; then
    local target=$(readlink -f "$local_models")
    if [ "$target" = "$MODELS_PATH" ]; then
      log_s "Models 软链接正确: $local_models -> $MODELS_PATH"
    else
      log_w "Models 软链接指向错误: $local_models -> $target (期望: $MODELS_PATH)"
    fi
  elif [ -d "$local_models" ]; then
    log_w "Models 是普通目录而非软链接，请手动设置软链接"
  else
    log_w "Models 目录不存在: $local_models"
  fi
}

check_and_kill_port() {
  local port="$1"
  log_i "检查端口 $port ..."

  local pids=""
  if command -v ss >/dev/null 2>&1; then
    pids=$(ss -tlpn 2>/dev/null \
      | awk -v p=":$port" '$4 ~ p {print $7}' \
      | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' \
      | xargs -r echo) || true
  fi

  if [ -z "$pids" ] && command -v lsof >/dev/null 2>&1; then
    pids=$(timeout 2s lsof -nP -ti TCP:"$port" 2>/dev/null | xargs -r echo) || true
  fi

  if [ -z "$pids" ] && command -v fuser >/dev/null 2>&1; then
    pids=$(fuser -n tcp "$port" 2>/dev/null | xargs -r echo) || true
  fi

  if [ -n "$pids" ]; then
    log_w "端口 $port 被占用: $pids，尝试终止"
    kill -TERM $pids 2>/dev/null || true
    sleep 1
    kill -KILL $pids 2>/dev/null || true
    sleep 0.5
    log_i "端口 $port 已释放"
  else
    log_i "端口 $port 未被占用"
  fi
}

stop_nginx(){
  if pgrep nginx >/dev/null 2>&1; then
    log_w "停止 nginx ..."
    sudo nginx -s stop 2>/dev/null || sudo pkill nginx || sudo pkill -9 nginx || true
    sleep 1
  fi
}

start_nginx(){
  if [ -f "$NGINX_CONF" ]; then
    log_i "启动 nginx 使用配置: $NGINX_CONF"
    sudo nginx -c "$NGINX_CONF" || log_w "nginx 启动失败（检查配置）"
    log_s "nginx 已启动"
  else
    log_w "未找到 nginx 配置文件: $NGINX_CONF，跳过 nginx 启动"
  fi
}

check_pytorch(){
  log_i "检查 PyTorch 环境..."
  # 注意：不能在启动前导入 torch，否则会导致 CUDA 分配器配置冲突
  # 只检查 PyTorch 是否安装，不初始化 CUDA
  python3 - <<'PY'
import sys
try:
    import importlib.util
    torch_spec = importlib.util.find_spec("torch")
    if torch_spec is None:
        print("✗ PyTorch not installed!")
        sys.exit(1)
    print("✓ PyTorch package found")
    # 不导入 torch，避免提前初始化 CUDA
except Exception as e:
    print(f"✗ PyTorch check failed: {e}")
    sys.exit(1)
PY
  if [ $? -eq 0 ]; then
    log_s "PyTorch 环境检查通过"
  else
    log_e "PyTorch 环境检查失败"
    exit 1
  fi
}

install_comfyui_deps(){
  log_i "安装 ComfyUI 核心依赖..."

  cd "$COMFYUI_PATH"

  if [ -f "requirements.txt" ]; then
    log_i "安装 requirements.txt ..."
    pip install -r requirements.txt --no-warn-script-location || log_w "部分依赖安装失败"
  fi

  # 安装常用扩展依赖
  log_i "安装常用依赖包..."
  pip install --upgrade pip setuptools wheel --no-warn-script-location || true
  pip install opencv-python pillow numpy scipy matplotlib scikit-image imageio || log_w "部分依赖安装失败"
  pip install transformers diffusers accelerate || log_w "部分 ML 包安装失败"

  log_s "ComfyUI 核心依赖安装完成"
}

install_custom_nodes_deps(){
  log_i "安装 custom_nodes 依赖..."

  cd "$COMFYUI_PATH"

  if [ ! -d "custom_nodes" ]; then
    log_w "custom_nodes 目录不存在，跳过"
    return 0
  fi

  for d in custom_nodes/*/; do
    [ -d "$d" ] || continue
    name=$(basename "$d")

    if [ -f "${d}requirements.txt" ]; then
      log_i "安装 ${name} 依赖..."
      pip install -r "${d}requirements.txt" --no-warn-script-location || log_w "${name} 依赖安装失败"
    fi

    if [ -f "${d}install.py" ]; then
      log_i "运行 ${name}/install.py ..."
      (cd "$d" && python3 install.py) || log_w "${name}/install.py 运行异常"
    fi

    if [ -f "${d}setup.py" ]; then
      log_i "pip install -e ${name}"
      (cd "$d" && pip install -e . --no-warn-script-location) || log_w "${name} setup.py 失败"
    fi
  done

  log_s "custom_nodes 依赖安装完成"
}

prepare_dirs(){
  log_i "准备工作目录..."
  mkdir -p "$COMFYUI_PATH/custom_nodes/ComfyUI-Manager/.cache" \
           "$COMFYUI_PATH/output" \
           "$COMFYUI_PATH/input" \
           "$COMFYUI_PATH/temp"

  log_s "目录准备完成"
}

show_env_info(){
  log_i "======== 环境信息 ========"
  echo "虚拟环境: $VENV_PATH"
  echo "Python: $(which python3)"
  echo "ComfyUI: $COMFYUI_PATH"
  echo "Models: $MODELS_PATH"
  echo "Nginx 配置: $NGINX_CONF"
  echo "日志: $LOG_FILE"
  log_i "========================="
}

# ===== 主流程 =====
log_i "======== ComfyUI 多 GPU 环境启动 ========"

# 检查 ComfyUI 目录
if [ ! -d "$COMFYUI_PATH" ]; then
  log_e "ComfyUI 目录不存在: $COMFYUI_PATH"
  exit 1
fi

# 1. 安装必要的系统依赖
install_apt_if_needed

# 2. 检查 models 软链接
check_models_symlink

# 3. 检查 PyTorch 环境
check_pytorch

# 4. 准备目录
prepare_dirs

# 5. 安装 ComfyUI 依赖
install_comfyui_deps

# 6. 安装 custom_nodes 依赖
install_custom_nodes_deps

# 7. 端口清理
check_and_kill_port 7860
check_and_kill_port 8188

# 8. nginx 管理
stop_nginx
start_nginx

# 9. 日志清理（保留 7 天）
find "$LOG_DIR" -name "comfyui_*.log" -type f -mtime +7 -delete 2>/dev/null || true

# 10. 显示环境信息
show_env_info

# 11. 启动 ComfyUI
cd "$COMFYUI_PATH"
log_s "启动 ComfyUI (多 GPU 模式) ..."
echo "$(date '+%F %T') - ComfyUI 启动" >> "$LOG_FILE"

# 启动参数：
# --listen 0.0.0.0: 监听所有网络接口
# --port 7860: 主端口
# --enable-cors-header: 启用 CORS
# --disable-metadata: 禁用元数据（可选）
# 可以添加其他参数如 --highvram, --normalvram 等

exec python3 main.py \
  --listen 0.0.0.0 \
  --port 7860 \
  --enable-cors-header \
  --disable-metadata \
  "$@" 2>&1 | tee -a "$LOG_FILE"
