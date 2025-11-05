# ComfyUI 多 GPU 并行改造方案 - 执行文档 v3.5

> **作者**: Claude (Anthropic)
> **版本**: 3.5 终极版
> **日期**: 2025-11-05
> **目标**: 单进程 + 共享 RAM + 4 GPU 真并行 + 零改 workflow

---

## 📋 目录

1. [方案概述](#1-方案概述)
2. [环境要求](#2-环境要求)
3. [改动清单](#3-改动清单)
4. [实施步骤](#4-实施步骤)
   - [Step 1: 备份与准备](#step-1-备份与准备)
   - [Step 2: 修改 model_management.py](#step-2-修改-model_managementpy)
   - [Step 3: 修改 main.py](#step-3-修改-mainpy)
   - [Step 4: 修改 server.py](#step-4-修改-serverpy)
   - [Step 5: 配置 Nginx](#step-5-配置-nginx)
   - [Step 6: 测试验证](#step-6-测试验证)
5. [验收标准](#5-验收标准)
6. [回滚方案](#6-回滚方案)
7. [故障排查](#7-故障排查)
8. [性能调优](#8-性能调优)

---

## 1. 方案概述

### 1.1 核心目标

| 目标 | 实现方式 |
|------|---------|
| **共享 RAM** | 单进程运行，模型权重在 RAM 中只存一份 |
| **4 GPU 并行** | 4 个队列 + 4 个 worker 线程，各自绑定一张 GPU |
| **固定路由** | 每个入口（端口/路径）固定映射到一张 GPU |
| **零改 workflow** | 不修改现有模板，完全兼容 |
| **可回滚** | 环境变量开关，随时恢复原有单 GPU 模式 |

### 1.2 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                    Nginx/Caddy 反向代理                        │
│  :8181 (GPU 0) :8182 (GPU 1) :8183 (GPU 2) :8184 (GPU 3)    │
└────────────┬────────────┬────────────┬────────────┬──────────┘
             │ X-TARGET-GPU: 0         │            │
             │            │ X-TARGET-GPU: 1         │
             │            │            │ X-TARGET-GPU: 2
             │            │            │            │ X-TARGET-GPU: 3
             ▼            ▼            ▼            ▼
    ┌────────────────────────────────────────────────────┐
    │         ComfyUI 单进程 (:8188)                       │
    │  ┌─────────────────────────────────────────────┐   │
    │  │  /prompt handler (路由分发)                   │   │
    │  └──┬──────────┬──────────┬──────────┬─────────┘   │
    │     │          │          │          │             │
    │  ┌──▼───┐  ┌──▼───┐  ┌──▼───┐  ┌──▼───┐         │
    │  │Queue0│  │Queue1│  │Queue2│  │Queue3│         │
    │  └──┬───┘  └──┬───┘  └──┬───┘  └──┬───┘         │
    │     │          │          │          │             │
    │  ┌──▼────┐ ┌──▼────┐ ┌──▼────┐ ┌──▼────┐        │
    │  │Worker0│ │Worker1│ │Worker2│ │Worker3│        │
    │  │ GPU:0 │ │ GPU:1 │ │ GPU:2 │ │ GPU:3 │        │
    │  └───────┘ └───────┘ └───────┘ └───────┘        │
    │                                                    │
    │  ┌──────────────────────────────────────────┐    │
    │  │  模型缓存（按设备分区）                     │    │
    │  │  device_cache[0] → [models on GPU 0]     │    │
    │  │  device_cache[1] → [models on GPU 1]     │    │
    │  │  device_cache[2] → [models on GPU 2]     │    │
    │  │  device_cache[3] → [models on GPU 3]     │    │
    │  └──────────────────────────────────────────┘    │
    └────────────────────────────────────────────────────┘
             │              │              │
    ┌────────▼─────┐ ┌──────▼──────┐ ┌────▼────────┐
    │   GPU 0      │ │   GPU 1     │ │   GPU 2/3   │
    │  VRAM 24GB   │ │  VRAM 24GB  │ │  VRAM 24GB  │
    └──────────────┘ └─────────────┘ └─────────────┘

         ▲              ▲              ▲
         └──────────────┴──────────────┘
              共享 RAM 中的模型权重
              (只加载一次，4 GPU 共享)
```

### 1.3 核心特性

- ✅ **特性开关**: `COMFY_MULTI_GPU_SCHED=1` 启用，`=0` 回退原模式
- ✅ **设备分区缓存**: 避免多线程竞争，支持真并行
- ✅ **观测指标**: 每 GPU 的队列长度、等待时间、执行时间、OOM 次数
- ✅ **GPU 预热**: 启动时预分配显存，降低首次请求延迟
- ✅ **OOM 弹性**: 自动清缓存重试
- ✅ **兼容性检查**: 启动时扫描 custom_nodes 中的设备硬编码

---

## 2. 环境要求

### 2.1 硬件要求

| 组件 | 要求 |
|------|------|
| **GPU** | 4 张 NVIDIA GPU（推荐同型号，如 4x RTX 4090） |
| **VRAM** | 每卡至少 16GB（推荐 24GB+） |
| **RAM** | 至少 64GB（推荐 128GB+，取决于模型大小） |
| **存储** | SSD（模型文件读取性能关键） |

### 2.2 软件要求

| 组件 | 版本 |
|------|------|
| **Python** | 3.10+ |
| **PyTorch** | 2.0+ with CUDA |
| **CUDA** | 11.8+ |
| **ComfyUI** | 当前版本（已测试 v0.3.68） |
| **Nginx/Caddy** | 任意版本 |

### 2.3 检查命令

```bash
# 检查 GPU 数量
nvidia-smi --list-gpus

# 检查 CUDA
python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}, GPUs: {torch.cuda.device_count()}')"

# 检查 ComfyUI 版本
python main.py --version
```

---

## 3. 改动清单

### 3.1 文件改动概览

| 文件 | 改动类型 | 行数 | 风险等级 |
|------|---------|------|---------|
| `comfy/model_management.py` | 修改 | +80 | 中 |
| `main.py` | 修改 | +120 | 中 |
| `server.py` | 修改 | +15 | 低 |
| `nginx.conf` (新增) | 新建 | +40 | 低 |
| `start_multi_gpu.sh` (新增) | 新建 | +10 | 低 |

**总计**: ~265 行新增代码

### 3.2 不改动的内容

- ❌ 不改 workflow 模板
- ❌ 不改自定义节点（除非有硬编码问题）
- ❌ 不改 execution.py 核心执行逻辑
- ❌ 不改前端代码

---

## 4. 实施步骤

### Step 1: 备份与准备

#### 1.1 备份原始文件

```bash
cd /path/to/ComfyUI

# 创建备份目录
mkdir -p backups/$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="backups/$(date +%Y%m%d_%H%M%S)"

# 备份关键文件
cp comfy/model_management.py "$BACKUP_DIR/"
cp main.py "$BACKUP_DIR/"
cp server.py "$BACKUP_DIR/"

echo "Backup completed: $BACKUP_DIR"
```

#### 1.2 创建 Git 分支（如果使用 Git）

```bash
git checkout -b feature/multi-gpu-sched
git status
```

#### 1.3 设置环境变量（测试）

```bash
# 先不启用，确保原模式正常
export COMFY_MULTI_GPU_SCHED=0
export COMFY_NUM_GPUS=4

# 测试原有功能
python main.py --listen 0.0.0.0 --port 8188
# Ctrl+C 停止
```

---

### Step 2: 修改 model_management.py

#### 2.1 目标

- 添加按设备分区的模型缓存
- 修改 `free_memory()` 支持设备隔离
- 修改 `load_models_gpu()` 使用设备缓存
- 修改 `soft_empty_cache()` 支持按设备清理

#### 2.2 具体改动

**位置**: `comfy/model_management.py`

**在文件开头（约 line 30 附近）添加**:

```python
import os
import threading

# ============ 多 GPU 调度相关配置 ============
ENABLE_MULTI_GPU = os.getenv('COMFY_MULTI_GPU_SCHED', '0') == '1'

if ENABLE_MULTI_GPU:
    _current_loaded_models_by_device = {}  # device_id -> [LoadedModel]
    _model_cache_lock = threading.RLock()
    _use_device_cache = True
    logging.info("✅ Multi-GPU scheduling ENABLED")
else:
    _use_device_cache = False
    logging.info("ℹ️  Multi-GPU scheduling DISABLED (using default mode)")
```

**在 `current_loaded_models = []` 定义后（约 line 449）添加**:

```python
# 原有代码保持不变
current_loaded_models = []

# 新增：统一缓存访问入口
def _get_current_loaded_models(device=None):
    """
    统一缓存访问入口：根据开关自动返回正确的缓存

    Args:
        device: torch.device 对象，如果为 None 则使用当前设备

    Returns:
        list: 对应设备的 LoadedModel 列表
    """
    if _use_device_cache:
        if device is None:
            device = get_torch_device()

        # 提取设备 ID
        if hasattr(device, 'index') and device.index is not None:
            device_id = device.index
        elif hasattr(device, 'type') and device.type == 'cuda':
            device_id = torch.cuda.current_device()
        else:
            device_id = 0  # CPU 或其他设备统一用 0

        # 按设备分区
        with _model_cache_lock:
            if device_id not in _current_loaded_models_by_device:
                _current_loaded_models_by_device[device_id] = []
                logging.debug(f"Created model cache for device {device_id}")
            return _current_loaded_models_by_device[device_id]
    else:
        # 兼容原有模式
        global current_loaded_models
        return current_loaded_models
```

**修改 `free_memory()` 函数（约 line 580）**:

找到函数定义：
```python
def free_memory(memory_required, device, keep_loaded=[]):
```

在函数开头添加：
```python
def free_memory(memory_required, device, keep_loaded=[]):
    cleanup_models_gc()
    unloaded_model = []
    can_unload = []
    unloaded_models = []

    # ========== 新增：使用设备专属缓存 ==========
    current_loaded = _get_current_loaded_models(device)
    # =========================================

    # 原有逻辑继续，但使用 current_loaded 代替 current_loaded_models
    for i in range(len(current_loaded) - 1, -1, -1):
        shift_model = current_loaded[i]
        # ... 后续逻辑不变
```

**注意**: 将函数内所有的 `current_loaded_models` 替换为 `current_loaded`

**修改 `load_models_gpu()` 函数（约 line 617）**:

在函数中找到使用 `current_loaded_models` 的地方，改为使用设备缓存：

```python
def load_models_gpu(models, memory_required=0, force_patch_weights=False, minimum_memory_required=None, force_full_load=False):
    # ... 前面逻辑不变，直到 models_to_load = [] 后

    for loaded_model in models_to_load:
        # ========== 新增：获取设备专属缓存 ==========
        device = loaded_model.device
        current_loaded = _get_current_loaded_models(device)
        # =========================================

        # 检查是否已在当前设备缓存中
        try:
            loaded_model_index = current_loaded.index(loaded_model)
        except:
            loaded_model_index = None

        # ... 后续逻辑使用 current_loaded 代替 current_loaded_models
```

**修改 `soft_empty_cache()` 函数（约 line 1445）**:

```python
def soft_empty_cache(device=None):
    """
    清空 CUDA 缓存

    Args:
        device: 指定设备，如果为 None 则清空所有设备
    """
    global cpu_state

    # 新增：支持按设备清理
    if device is not None and hasattr(device, 'type'):
        if device.type == 'cuda':
            device_id = device.index if hasattr(device, 'index') else 0
            with torch.cuda.device(device):
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
            logging.debug(f"Cleared cache for device cuda:{device_id}")
            return
        elif device.type == 'xpu':
            torch.xpu.empty_cache()
            return
        elif device.type == 'npu':
            torch.npu.empty_cache()
            return
        elif device.type == 'mlu':
            torch.mlu.empty_cache()
            return

    # 原有逻辑：清空所有设备
    if cpu_state == CPUState.MPS:
        torch.mps.empty_cache()
    elif is_intel_xpu():
        torch.xpu.empty_cache()
    elif is_ascend_npu():
        torch.npu.empty_cache()
    elif is_mlu():
        torch.mlu.empty_cache()
    elif torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()
```

**修改 `loaded_models()` 函数（如果有）**:

```python
def loaded_models(only_currently_used=False, device=None):
    """
    获取已加载的模型列表

    Args:
        only_currently_used: 是否只返回正在使用的模型
        device: 指定设备，如果为 None 则返回所有设备的模型
    """
    if device is not None:
        # 返回指定设备的模型
        current_loaded = _get_current_loaded_models(device)
        if only_currently_used:
            return [x for x in current_loaded if x.currently_used]
        return current_loaded
    else:
        # 返回所有设备的模型
        if _use_device_cache:
            all_models = []
            with _model_cache_lock:
                for cache in _current_loaded_models_by_device.values():
                    all_models.extend(cache)
            if only_currently_used:
                return [x for x in all_models if x.currently_used]
            return all_models
        else:
            # 原有模式
            if only_currently_used:
                return [x for x in current_loaded_models if x.currently_used]
            return current_loaded_models
```

#### 2.3 验证改动

```bash
# 语法检查
python -m py_compile comfy/model_management.py

# 如果有错误会报出来
echo $?  # 应该返回 0
```

---

### Step 3: 修改 main.py

#### 3.1 目标

- 添加多 GPU worker 函数
- 添加观测指标类
- 添加 GPU 预热函数
- 添加兼容性检查函数
- 修改 `start_comfyui()` 支持多队列启动

#### 3.2 具体改动

**位置**: `main.py`

**在文件开头（import 部分后）添加**:

```python
import os
import gc
import time
from collections import deque

# ============ 多 GPU 调度配置 ============
ENABLE_MULTI_GPU = os.getenv('COMFY_MULTI_GPU_SCHED', '0') == '1'
NUM_GPUS = int(os.getenv('COMFY_NUM_GPUS', '4')) if ENABLE_MULTI_GPU else 1

if ENABLE_MULTI_GPU:
    logging.info(f"🚀 Multi-GPU mode ENABLED with {NUM_GPUS} GPUs")
else:
    logging.info("ℹ️  Single-GPU mode (default)")
```

**在 `prompt_worker()` 函数后添加新函数**:

```python
# ============ 多 GPU 观测指标 ============
class GPUQueueMetrics:
    """每个 GPU 队列的性能指标收集"""

    def __init__(self, gpu_id):
        self.gpu_id = gpu_id
        self.queue_lens = deque(maxlen=100)
        self.wait_times = deque(maxlen=100)
        self.exec_times = deque(maxlen=100)
        self.oom_count = 0
        self.total_tasks = 0
        self.success_tasks = 0
        self.failed_tasks = 0
        self.last_log_time = time.time()

    def record_task(self, queue_len, wait_ms, exec_ms, success=True, is_oom=False):
        """记录一次任务执行"""
        self.queue_lens.append(queue_len)
        self.wait_times.append(wait_ms)
        self.exec_times.append(exec_ms)

        if is_oom:
            self.oom_count += 1

        self.total_tasks += 1
        if success:
            self.success_tasks += 1
        else:
            self.failed_tasks += 1

        # 每 10 个任务或每 60 秒记录一次
        now = time.time()
        if self.total_tasks % 10 == 0 or (now - self.last_log_time) > 60:
            self.log_metrics()
            self.last_log_time = now

    def log_metrics(self):
        """输出统计指标"""
        if len(self.exec_times) == 0:
            return

        avg_queue = sum(self.queue_lens) / len(self.queue_lens)
        avg_wait = sum(self.wait_times) / len(self.wait_times)
        avg_exec = sum(self.exec_times) / len(self.exec_times)
        success_rate = (self.success_tasks / self.total_tasks * 100) if self.total_tasks > 0 else 0

        logging.info(
            f"📊 [GPU {self.gpu_id}] "
            f"tasks={self.total_tasks}, "
            f"success={success_rate:.1f}%, "
            f"queue={avg_queue:.1f}, "
            f"wait={avg_wait:.0f}ms, "
            f"exec={avg_exec:.0f}ms, "
            f"oom={self.oom_count}"
        )


# ============ 多 GPU Worker ============
def prompt_worker_gpu(gpu_id, queue, server_instance):
    """
    GPU 专用 worker 线程

    Args:
        gpu_id: GPU 设备 ID (0-3)
        queue: 该 GPU 的任务队列
        server_instance: 服务器实例
    """
    # 设置线程设备
    torch.cuda.set_device(gpu_id)
    logging.info(f"🔧 [GPU {gpu_id}] Worker started on cuda:{gpu_id}")

    # 创建专属执行器
    cache_type = execution.CacheType.CLASSIC
    if args.cache_lru > 0:
        cache_type = execution.CacheType.LRU
    elif args.cache_ram > 0:
        cache_type = execution.CacheType.RAM_PRESSURE
    elif args.cache_none:
        cache_type = execution.CacheType.NONE

    executor = execution.PromptExecutor(
        server_instance,
        cache_type=cache_type,
        cache_args={"lru": args.cache_lru, "ram": args.cache_ram}
    )

    # 初始化指标收集
    metrics = GPUQueueMetrics(gpu_id)

    # GC 相关
    last_gc_collect = 0
    need_gc = False
    gc_collect_interval = 10.0

    # 故障恢复
    consecutive_failures = 0
    max_consecutive_failures = 3

    current_time = 0.0

    while True:
        timeout = 1000.0
        if need_gc:
            current_time = time.perf_counter()
            timeout = max(gc_collect_interval - (current_time - last_gc_collect), 0.0)

        # 记录队列等待开始时间
        queue_start_time = time.perf_counter()
        queue_item = queue.get(timeout=timeout)

        if queue_item is not None:
            item, item_id = queue_item

            # 计算等待时间
            wait_ms = (time.perf_counter() - queue_start_time) * 1000
            queue_len = len(queue.queue)

            # 执行开始
            execution_start_time = time.perf_counter()
            prompt_id = item[1]
            server_instance.last_prompt_id = prompt_id

            is_oom = False
            success = False

            try:
                # 双重设备保险
                with torch.cuda.device(gpu_id):
                    sensitive = item[5]
                    extra_data = item[3].copy()
                    for k in sensitive:
                        extra_data[k] = sensitive[k]

                    executor.execute(item[2], prompt_id, extra_data, item[4])

                need_gc = True
                consecutive_failures = 0
                success = executor.success

                # 任务完成
                remove_sensitive = lambda prompt: prompt[:5] + prompt[6:]
                queue.task_done(
                    item_id,
                    executor.history_result,
                    status=execution.PromptQueue.ExecutionStatus(
                        status_str='success' if executor.success else 'error',
                        completed=executor.success,
                        messages=executor.status_messages
                    ),
                    process_item=remove_sensitive
                )

                if server_instance.client_id is not None:
                    server_instance.send_sync(
                        "executing",
                        {"node": None, "prompt_id": prompt_id},
                        server_instance.client_id
                    )

                current_time = time.perf_counter()
                execution_time = current_time - execution_start_time

                if execution_time > 600:
                    execution_time_str = time.strftime("%H:%M:%S", time.gmtime(execution_time))
                    logging.info(f"✅ [GPU {gpu_id}] Prompt {prompt_id[:8]} executed in {execution_time_str}")
                else:
                    logging.info(f"✅ [GPU {gpu_id}] Prompt {prompt_id[:8]} executed in {execution_time:.2f}s")

            except RuntimeError as e:
                # OOM 处理
                if "out of memory" in str(e).lower() or "OOM" in str(e):
                    is_oom = True
                    logging.error(f"💥 [GPU {gpu_id}] OOM for prompt {prompt_id[:8]}, clearing cache")

                    # 清理当前设备缓存
                    device = torch.device(f'cuda:{gpu_id}')
                    comfy.model_management.soft_empty_cache(device)
                    gc.collect()

                logging.error(f"❌ [GPU {gpu_id}] Error executing prompt {prompt_id[:8]}: {e}")
                consecutive_failures += 1

                queue.task_done(
                    item_id,
                    {},
                    status=execution.PromptQueue.ExecutionStatus(
                        status_str='error',
                        completed=False,
                        messages=[str(e)]
                    )
                )

                # 连续失败过多时重建执行器
                if consecutive_failures >= max_consecutive_failures:
                    logging.warning(f"⚠️  [GPU {gpu_id}] Too many failures ({consecutive_failures}), recreating executor")
                    executor = execution.PromptExecutor(
                        server_instance,
                        cache_type=cache_type,
                        cache_args={"lru": args.cache_lru, "ram": args.cache_ram}
                    )
                    consecutive_failures = 0

            except Exception as e:
                logging.error(f"❌ [GPU {gpu_id}] Unexpected error: {e}")
                import traceback
                traceback.print_exc()

                consecutive_failures += 1

                queue.task_done(
                    item_id,
                    {},
                    status=execution.PromptQueue.ExecutionStatus(
                        status_str='error',
                        completed=False,
                        messages=[str(e)]
                    )
                )

            # 记录指标
            exec_ms = (time.perf_counter() - execution_start_time) * 1000
            metrics.record_task(queue_len, wait_ms, exec_ms, success=success, is_oom=is_oom)

        # GC 和内存管理
        flags = queue.get_flags()
        free_memory_flag = flags.get("free_memory", False)

        if flags.get("unload_models", free_memory_flag):
            comfy.model_management.unload_all_models()
            need_gc = True
            last_gc_collect = 0

        if free_memory_flag:
            executor.reset()
            need_gc = True
            last_gc_collect = 0

        if need_gc:
            current_time = time.perf_counter()
            if (current_time - last_gc_collect) > gc_collect_interval:
                gc.collect()
                device = torch.device(f'cuda:{gpu_id}')
                comfy.model_management.soft_empty_cache(device)
                last_gc_collect = current_time
                need_gc = False
                hook_breaker_ac10a0.restore_functions()


# ============ GPU 预热 ============
def warmup_gpu(gpu_id):
    """
    预热 GPU：预分配显存，降低首次请求延迟

    Args:
        gpu_id: GPU 设备 ID
    """
    logging.info(f"🔥 [GPU {gpu_id}] Starting warmup...")

    try:
        torch.cuda.set_device(gpu_id)

        with torch.cuda.device(gpu_id):
            # 简单的 tensor 分配触发 CUDA 初始化
            dummy = torch.zeros((1000, 1000), device=f'cuda:{gpu_id}', dtype=torch.float32)
            torch.cuda.synchronize()
            del dummy

        logging.info(f"✅ [GPU {gpu_id}] Warmup completed")
    except Exception as e:
        logging.warning(f"⚠️  [GPU {gpu_id}] Warmup failed: {e}")


# ============ 兼容性检查 ============
def check_custom_nodes_compatibility():
    """
    检查自定义节点中的设备硬编码问题
    扫描 custom_nodes 目录，查找可能的 cuda:0 硬编码
    """
    if not ENABLE_MULTI_GPU:
        return

    logging.info("🔍 Checking custom nodes compatibility...")

    try:
        import re
        from pathlib import Path

        custom_nodes_paths = folder_paths.get_folder_paths("custom_nodes")
        if not custom_nodes_paths:
            return

        custom_nodes_path = Path(custom_nodes_paths[0])
        if not custom_nodes_path.exists():
            return

        issues = []

        # 匹配模式：排除字符串和注释中的误报
        # 查找 cuda:0 但不在引号内的情况
        pattern = re.compile(r'''(?<!['"(])cuda:0(?!['")])''')

        for py_file in custom_nodes_path.rglob("*.py"):
            if py_file.name.startswith('.'):
                continue

            try:
                content = py_file.read_text(encoding='utf-8', errors='ignore')

                for i, line in enumerate(content.split('\n'), 1):
                    # 跳过注释行
                    stripped = line.strip()
                    if stripped.startswith('#'):
                        continue

                    if pattern.search(line):
                        rel_path = py_file.relative_to(custom_nodes_path)
                        issues.append(f"{rel_path}:{i}")

                        if len(issues) >= 20:  # 最多收集 20 个
                            break
            except Exception:
                pass

            if len(issues) >= 20:
                break

        if issues:
            logging.warning("=" * 70)
            logging.warning("⚠️  Found potential device hardcoding in custom nodes:")
            for issue in issues[:10]:
                logging.warning(f"  - {issue}")
            if len(issues) > 10:
                logging.warning(f"  ... and {len(issues) - 10} more")
            logging.warning("")
            logging.warning("These nodes may not work correctly with multi-GPU setup.")
            logging.warning("Please check if these are false positives or need fixing.")
            logging.warning("=" * 70)
        else:
            logging.info("✅ No obvious device hardcoding detected")

    except Exception as e:
        logging.warning(f"⚠️  Compatibility check failed: {e}")
```

**修改 `start_comfyui()` 函数（约 line 303）**:

找到这部分代码：
```python
def start_comfyui(asyncio_loop=None):
    # ... 前面逻辑不变，直到这里：

    prompt_server = server.PromptServer(asyncio_loop)

    # ========== 修改开始 ==========
```

替换为：
```python
    prompt_server = server.PromptServer(asyncio_loop)

    # ========== 多 GPU 模式初始化 ==========
    if ENABLE_MULTI_GPU:
        # 创建多个队列
        prompt_server.prompt_queues = [
            execution.PromptQueue(prompt_server) for _ in range(NUM_GPUS)
        ]
        # 兼容原有代码（指向 GPU 0 的队列）
        prompt_server.prompt_queue = prompt_server.prompt_queues[0]

        logging.info(f"📋 Created {NUM_GPUS} task queues for multi-GPU scheduling")
    else:
        # 单 GPU 模式（原有逻辑）
        prompt_server.prompt_queue = execution.PromptQueue(prompt_server)
    # ========================================
```

找到启动 worker 的代码（约 line 339）：
```python
    threading.Thread(target=prompt_worker, daemon=True, args=(prompt_server.prompt_queue, prompt_server,)).start()
```

替换为：
```python
    # ========== 启动 Worker 线程 ==========
    if ENABLE_MULTI_GPU:
        # 多 GPU 模式：启动多个 worker
        for gpu_id in range(NUM_GPUS):
            threading.Thread(
                target=prompt_worker_gpu,
                daemon=True,
                args=(gpu_id, prompt_server.prompt_queues[gpu_id], prompt_server),
                name=f"GPU{gpu_id}-Worker"
            ).start()
            logging.info(f"🚀 Started worker thread for GPU {gpu_id}")

        # 预热所有 GPU
        for gpu_id in range(NUM_GPUS):
            warmup_gpu(gpu_id)

        # 兼容性检查
        check_custom_nodes_compatibility()
    else:
        # 单 GPU 模式（原有逻辑）
        threading.Thread(
            target=prompt_worker,
            daemon=True,
            args=(prompt_server.prompt_queue, prompt_server)
        ).start()
    # ======================================
```

#### 3.3 验证改动

```bash
# 语法检查
python -m py_compile main.py

echo $?  # 应该返回 0
```

---

### Step 4: 修改 server.py

#### 4.1 目标

- 修改 `/prompt` 接口，支持 GPU 路由分发
- 从请求头 `X-TARGET-GPU` 读取目标 GPU
- 将任务提交到对应的队列

#### 4.2 具体改动

**位置**: `server.py`

找到 `/prompt` 路由处理函数（搜索 `@routes.post("/prompt")`，约 line 200-300 之间）

在函数开头添加 GPU 路由逻辑：

```python
@routes.post("/prompt")
async def post_prompt(request):
    # ========== 新增：GPU 路由 ==========
    # 解析目标 GPU ID
    gpu_id_str = request.headers.get('X-TARGET-GPU', '0')
    try:
        gpu_id = int(gpu_id_str)
        gpu_id = max(0, min(gpu_id, 3))  # 限制在 0-3
    except ValueError:
        gpu_id = 0
    # ==================================

    json_data = await request.json()
    json_data = DictX(json_data)

    # ... 现有的验证逻辑 ...

    # ========== 修改：选择队列 ==========
    # 原有代码类似：
    # prompt_id = str(uuid.uuid4())
    # self.prompt_queue.put((number, prompt_id, prompt, extra_data, outputs_to_execute, sensitive))

    # 修改为：
    prompt_id = str(uuid.uuid4())

    # 根据是否启用多 GPU 选择队列
    if hasattr(self, 'prompt_queues') and len(self.prompt_queues) > gpu_id:
        target_queue = self.prompt_queues[gpu_id]
        logging.debug(f"Routing prompt {prompt_id[:8]} to GPU {gpu_id}")
    else:
        # 向后兼容：单队列模式
        target_queue = self.prompt_queue
        gpu_id = 0

    number = self.number
    target_queue.put((number, prompt_id, prompt, extra_data, outputs_to_execute, sensitive))
    self.number += 1
    # ==================================

    # 返回响应（新增 gpu_id 字段）
    response = {
        "prompt_id": prompt_id,
        "number": number,
        "node_errors": valid[3]
    }

    # 如果是多 GPU 模式，返回分配的 GPU ID
    if hasattr(self, 'prompt_queues'):
        response["gpu_id"] = gpu_id

    return web.json_response(response)
```

#### 4.3 验证改动

```bash
# 语法检查
python -m py_compile server.py

echo $?  # 应该返回 0
```

---

### Step 5: 配置 Nginx

#### 5.1 目标

- 配置 4 个端口（8181-8184）对应 4 张 GPU
- 每个端口添加请求头 `X-TARGET-GPU`
- 配置限流防止过载

#### 5.2 创建 Nginx 配置

**创建文件**: `/etc/nginx/sites-available/comfyui-multi-gpu` 或 `./nginx.conf`

```nginx
# ComfyUI 多 GPU 负载均衡配置

# 限流配置：为每个 GPU 定义独立的限流区
limit_req_zone $binary_remote_addr zone=gpu0_limit:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=gpu1_limit:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=gpu2_limit:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=gpu3_limit:10m rate=10r/s;

# ComfyUI 后端
upstream comfyui_backend {
    server 127.0.0.1:8188;
    keepalive 32;
}

# GPU 0 入口 - 端口 8181
server {
    listen 8181;
    server_name _;

    client_max_body_size 100M;

    location / {
        # 限流：每秒 10 个请求，突发 20 个
        limit_req zone=gpu0_limit burst=20 nodelay;

        # 设置目标 GPU
        proxy_set_header X-TARGET-GPU 0;

        # 标准代理配置
        proxy_pass http://comfyui_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 超时设置（生图可能需要很长时间）
        proxy_read_timeout 600s;
        proxy_connect_timeout 60s;
        proxy_send_timeout 600s;
    }
}

# GPU 1 入口 - 端口 8182
server {
    listen 8182;
    server_name _;

    client_max_body_size 100M;

    location / {
        limit_req zone=gpu1_limit burst=20 nodelay;
        proxy_set_header X-TARGET-GPU 1;

        proxy_pass http://comfyui_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_read_timeout 600s;
        proxy_connect_timeout 60s;
        proxy_send_timeout 600s;
    }
}

# GPU 2 入口 - 端口 8183
server {
    listen 8183;
    server_name _;

    client_max_body_size 100M;

    location / {
        limit_req zone=gpu2_limit burst=20 nodelay;
        proxy_set_header X-TARGET-GPU 2;

        proxy_pass http://comfyui_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_read_timeout 600s;
        proxy_connect_timeout 60s;
        proxy_send_timeout 600s;
    }
}

# GPU 3 入口 - 端口 8184
server {
    listen 8184;
    server_name _;

    client_max_body_size 100M;

    location / {
        limit_req zone=gpu3_limit burst=20 nodelay;
        proxy_set_header X-TARGET-GPU 3;

        proxy_pass http://comfyui_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_read_timeout 600s;
        proxy_connect_timeout 60s;
        proxy_send_timeout 600s;
    }
}
```

#### 5.3 启用配置

```bash
# 如果使用系统 Nginx
sudo ln -s /etc/nginx/sites-available/comfyui-multi-gpu /etc/nginx/sites-enabled/
sudo nginx -t  # 测试配置
sudo systemctl reload nginx

# 如果使用本地 Nginx
nginx -c /path/to/nginx.conf -t  # 测试
nginx -c /path/to/nginx.conf     # 启动
```

#### 5.4 替代方案：使用路径前缀

如果不想开 4 个端口，可以用路径前缀：

```nginx
server {
    listen 8180;
    server_name _;

    client_max_body_size 100M;

    location /gpu0/ {
        limit_req zone=gpu0_limit burst=20 nodelay;
        proxy_set_header X-TARGET-GPU 0;
        rewrite ^/gpu0/(.*) /$1 break;
        proxy_pass http://comfyui_backend;
        # ... 其他配置同上
    }

    location /gpu1/ {
        limit_req zone=gpu1_limit burst=20 nodelay;
        proxy_set_header X-TARGET-GPU 1;
        rewrite ^/gpu1/(.*) /$1 break;
        proxy_pass http://comfyui_backend;
        # ... 其他配置同上
    }

    # 类似配置 /gpu2/, /gpu3/
}
```

---

### Step 6: 测试验证

#### 6.1 启动 ComfyUI（多 GPU 模式）

创建启动脚本 `start_multi_gpu.sh`:

```bash
#!/bin/bash

# 设置环境变量
export CUDA_VISIBLE_DEVICES=0,1,2,3
export COMFY_MULTI_GPU_SCHED=1
export COMFY_NUM_GPUS=4

# 限制 CPU 线程数（可选）
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4

# 启动 ComfyUI
python main.py --listen 0.0.0.0 --port 8188
```

```bash
chmod +x start_multi_gpu.sh
./start_multi_gpu.sh
```

**检查启动日志**，应该看到：

```
✅ Multi-GPU scheduling ENABLED
📋 Created 4 task queues for multi-GPU scheduling
🚀 Started worker thread for GPU 0
🚀 Started worker thread for GPU 1
🚀 Started worker thread for GPU 2
🚀 Started worker thread for GPU 3
🔥 [GPU 0] Starting warmup...
✅ [GPU 0] Warmup completed
🔥 [GPU 1] Starting warmup...
✅ [GPU 1] Warmup completed
...
🔍 Checking custom nodes compatibility...
✅ No obvious device hardcoding detected
```

#### 6.2 基础功能测试

**测试 1: 单个请求到不同 GPU**

```bash
# 测试 GPU 0（端口 8181）
curl -X POST http://localhost:8181/prompt \
  -H "Content-Type: application/json" \
  -d '{"prompt": {...}}'  # 你的 workflow JSON

# 测试 GPU 1（端口 8182）
curl -X POST http://localhost:8182/prompt \
  -H "Content-Type: application/json" \
  -d '{"prompt": {...}}'
```

**检查日志**，应该看到：
```
Routing prompt xxxxx to GPU 0
✅ [GPU 0] Prompt xxxxx executed in 5.23s

Routing prompt yyyyy to GPU 1
✅ [GPU 1] Prompt yyyyy executed in 5.18s
```

**测试 2: 并发请求**

```bash
# 同时向 4 个端口发送请求
for i in {0..3}; do
  port=$((8181 + i))
  curl -X POST http://localhost:$port/prompt \
    -H "Content-Type: application/json" \
    -d '{"prompt": {...}}' &
done
wait
```

**检查 GPU 使用**：
```bash
watch -n 1 nvidia-smi
```

应该看到 4 张 GPU 同时有负载。

#### 6.3 RAM 共享验证

```bash
# 记录启动时的 RSS
ps aux | grep "python main.py"
# 记住 RSS 列的值（例如 8.5GB）

# 发送 10 个请求（相同模型）
for i in {1..10}; do
  curl -X POST http://localhost:8181/prompt -H "Content-Type: application/json" -d '{"prompt": {...}}'
done

# 再次检查 RSS
ps aux | grep "python main.py"
# RSS 应该没有明显增长（增长 <10%）
```

#### 6.4 回滚测试

```bash
# 停止多 GPU 模式
# Ctrl+C

# 启动单 GPU 模式
export COMFY_MULTI_GPU_SCHED=0
python main.py --listen 0.0.0.0 --port 8188
```

**检查日志**，应该看到：
```
ℹ️  Multi-GPU scheduling DISABLED (using default mode)
ℹ️  Single-GPU mode (default)
```

发送请求应该正常工作（走原有逻辑）。

---

## 5. 验收标准

### 5.1 功能验收

| 编号 | 验收项 | 验证方法 | 预期结果 |
|------|-------|---------|---------|
| 1 | 单进程运行 | `ps aux \| grep python` | 只有 1 个 Python 进程 |
| 2 | 4 GPU 并行 | 同时发 4 个请求 + `nvidia-smi` | 4 卡同时有负载 |
| 3 | 固定路由 | 多次请求同一端口 + 日志 | 总是路由到相同 GPU |
| 4 | RAM 共享 | 重复提交相同模型 + `ps aux` | RSS 增长 <10% |
| 5 | 模板兼容 | 使用现有 workflow 测试 | 无需修改即可运行 |
| 6 | 回滚功能 | `COMFY_MULTI_GPU_SCHED=0` 启动 | 恢复单 GPU 模式 |

### 5.2 性能验收

| 指标 | 目标 | 验证方法 |
|------|------|---------|
| **吞吐量** | 提升 3.5-4x | 压测对比单/多 GPU |
| **首次延迟** | <2s（预热后） | 测量第一个请求响应时间 |
| **并发稳定性** | 无崩溃 | 持续运行 1 小时 |
| **OOM 恢复** | 自动清缓存 | 故意触发 OOM，检查是否恢复 |

### 5.3 日志验收

启动后应看到：
```
✅ Multi-GPU scheduling ENABLED
📋 Created 4 task queues
🚀 Started worker thread for GPU 0/1/2/3
🔥 [GPU x] Starting warmup...
✅ [GPU x] Warmup completed
🔍 Checking custom nodes compatibility...
```

运行时应定期看到：
```
📊 [GPU 0] tasks=10, success=100.0%, queue=0.5, wait=12ms, exec=5234ms, oom=0
```

### 5.4 兼容性验收

- ✅ 现有前端无需修改
- ✅ 现有 workflow JSON 无需修改
- ✅ 现有自定义节点正常工作（除非有硬编码）

---

## 6. 回滚方案

### 6.1 快速回滚（环境变量）

```bash
# 方法 1：修改启动脚本
export COMFY_MULTI_GPU_SCHED=0
python main.py --listen 0.0.0.0 --port 8188

# 方法 2：修改 systemd service
sudo systemctl edit comfyui
# 添加：
# [Service]
# Environment="COMFY_MULTI_GPU_SCHED=0"
sudo systemctl restart comfyui
```

### 6.2 代码回滚

```bash
# 从备份恢复
BACKUP_DIR="backups/20251105_123456"  # 你的备份目录

cp "$BACKUP_DIR/model_management.py" comfy/
cp "$BACKUP_DIR/main.py" .
cp "$BACKUP_DIR/server.py" .

# 重启服务
python main.py --listen 0.0.0.0 --port 8188
```

### 6.3 Git 回滚

```bash
git checkout main
git branch -D feature/multi-gpu-sched
```

---

## 7. 故障排查

### 7.1 常见问题

#### 问题 1: 启动时报错 `NameError: name '_get_current_loaded_models' is not defined`

**原因**: model_management.py 改动不完整

**解决**:
```bash
# 检查是否添加了函数定义
grep "_get_current_loaded_models" comfy/model_management.py

# 如果没有，重新添加（参考 Step 2）
```

#### 问题 2: 所有请求都路由到 GPU 0

**原因**: Nginx 未正确设置 Header

**解决**:
```bash
# 检查 Nginx 配置
nginx -T | grep X-TARGET-GPU

# 应该看到：
# proxy_set_header X-TARGET-GPU 0;
# proxy_set_header X-TARGET-GPU 1;
# ...

# 重新加载 Nginx
sudo nginx -s reload
```

#### 问题 3: GPU 1/2/3 没有负载

**原因**: Worker 线程未启动或崩溃

**解决**:
```bash
# 检查日志中是否有 Worker 启动信息
grep "Worker started" comfyui.log

# 检查是否有异常堆栈
grep "Traceback" comfyui.log

# 检查线程状态
python -c "
import os
os.environ['COMFY_MULTI_GPU_SCHED'] = '1'
exec(open('main.py').read())
" | grep "GPU.*Worker"
```

#### 问题 4: RAM 持续增长

**原因**: 模型缓存未正确清理

**解决**:
```bash
# 检查 free_memory 是否被调用
grep "Unloading" comfyui.log

# 手动触发清理
curl -X POST http://localhost:8188/free -d '{"unload_models": true}'

# 检查缓存分区是否生效
python -c "
import comfy.model_management as mm
print('Using device cache:', mm._use_device_cache)
"
```

#### 问题 5: OOM 后崩溃

**原因**: OOM 处理未生效

**检查日志**:
```bash
grep "OOM" comfyui.log
# 应该看到：
# 💥 [GPU x] OOM for prompt..., clearing cache
```

**如果没有**，检查代码中是否正确捕获了 `RuntimeError`。

### 7.2 调试工具

#### 查看队列状态

```python
# 添加调试接口到 server.py
@routes.get("/debug/queues")
async def get_queue_status(request):
    if not hasattr(self, 'prompt_queues'):
        return web.json_response({"mode": "single-gpu"})

    status = []
    for i, q in enumerate(self.prompt_queues):
        running, queued = q.get_current_queue_volatile()
        status.append({
            "gpu_id": i,
            "queued": len(queued),
            "running": len(running)
        })

    return web.json_response({"queues": status})
```

```bash
# 查询
curl http://localhost:8188/debug/queues
```

#### 查看模型缓存

```python
# 在 Python REPL 中
import comfy.model_management as mm

if mm._use_device_cache:
    for device_id, cache in mm._current_loaded_models_by_device.items():
        print(f"GPU {device_id}: {len(cache)} models")
        for m in cache:
            print(f"  - {m.model.model.__class__.__name__}")
```

---

## 8. 性能调优

### 8.1 调整每卡并发

```bash
# 如果显存足够（>24GB），可以尝试每卡并发 2
# 修改 prompt_worker_gpu 创建 2 个实例

# 或者使用队列优先级（未实现，可扩展）
```

### 8.2 调整 GC 间隔

```python
# main.py prompt_worker_gpu 函数中
gc_collect_interval = 10.0  # 默认 10 秒

# 如果内存压力大，改为 5.0
# 如果追求性能，改为 15.0
```

### 8.3 调整限流参数

```nginx
# nginx.conf
limit_req zone=gpu0_limit burst=20 nodelay;

# 如果队列堆积，降低 burst
# burst=10

# 如果拒绝率高，提高 rate
# rate=15r/s
```

### 8.4 监控指标

```bash
# 实时监控 GPU
watch -n 1 nvidia-smi

# 实时监控日志
tail -f comfyui.log | grep "📊"

# 统计 OOM 次数
grep "OOM" comfyui.log | wc -l

# 统计成功率
grep "✅.*executed" comfyui.log | wc -l
grep "❌.*Error" comfyui.log | wc -l
```

---

## 附录 A: 完整启动脚本示例

```bash
#!/bin/bash
# start_multi_gpu.sh

set -e

echo "🚀 Starting ComfyUI Multi-GPU Mode"

# 环境变量
export CUDA_VISIBLE_DEVICES=0,1,2,3
export COMFY_MULTI_GPU_SCHED=1
export COMFY_NUM_GPUS=4

# CPU 线程限制
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4

# 可选：启用 CUDA 优化
export CUDA_LAUNCH_BLOCKING=0

# 日志
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/comfyui_$(date +%Y%m%d_%H%M%S).log"

# 启动
echo "📝 Log file: $LOG_FILE"
python main.py \
    --listen 0.0.0.0 \
    --port 8188 \
    2>&1 | tee "$LOG_FILE"
```

---

## 附录 B: 压测脚本示例

```bash
#!/bin/bash
# benchmark.sh

# 测试单 GPU 吞吐量
echo "Testing single-GPU mode..."
export COMFY_MULTI_GPU_SCHED=0
python main.py --port 8188 &
PID=$!
sleep 10

# 发送 20 个请求
time for i in {1..20}; do
  curl -s -X POST http://localhost:8188/prompt \
    -H "Content-Type: application/json" \
    -d @workflow.json > /dev/null
done

kill $PID

# 测试多 GPU 吞吐量
echo "Testing multi-GPU mode..."
export COMFY_MULTI_GPU_SCHED=1
python main.py --port 8188 &
PID=$!
sleep 10

# 并发发送 20 个请求（每个 GPU 5 个）
time for i in {0..3}; do
  port=$((8181 + i))
  for j in {1..5}; do
    curl -s -X POST http://localhost:$port/prompt \
      -H "Content-Type: application/json" \
      -d @workflow.json > /dev/null &
  done
done
wait

kill $PID
```

---

## 附录 C: Systemd 服务配置

```ini
# /etc/systemd/system/comfyui-multi-gpu.service

[Unit]
Description=ComfyUI Multi-GPU Service
After=network.target

[Service]
Type=simple
User=comfyui
WorkingDirectory=/home/comfyui/ComfyUI
Environment="CUDA_VISIBLE_DEVICES=0,1,2,3"
Environment="COMFY_MULTI_GPU_SCHED=1"
Environment="COMFY_NUM_GPUS=4"
Environment="OMP_NUM_THREADS=4"
ExecStart=/usr/bin/python3 main.py --listen 0.0.0.0 --port 8188
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
# 启用服务
sudo systemctl daemon-reload
sudo systemctl enable comfyui-multi-gpu
sudo systemctl start comfyui-multi-gpu
sudo systemctl status comfyui-multi-gpu
```

---

## 总结

本文档提供了 ComfyUI 多 GPU 并行改造的完整实施方案，包括：

- ✅ 详细的改动步骤（Step 1-6）
- ✅ 完整的验收标准
- ✅ 可靠的回滚方案
- ✅ 全面的故障排查指南
- ✅ 性能调优建议

按照本文档逐步执行，预计 2-4 小时可完成改造，实现：
- 单进程共享 RAM
- 4 GPU 真并行
- 吞吐量提升 3.5-4x
- 零风险可回滚

**下一步**: 从 [Step 1: 备份与准备](#step-1-备份与准备) 开始执行。
