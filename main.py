import comfy.options
comfy.options.enable_args_parsing()

import os
import importlib.util
import folder_paths
import time
from comfy.cli_args import args
from app.logger import setup_logger
import itertools
import utils.extra_config
import logging
import sys
from comfy_execution.progress import get_progress_state
from comfy_execution.utils import get_executing_context
from comfy_api import feature_flags

if __name__ == "__main__":
    #NOTE: These do not do anything on core ComfyUI, they are for custom nodes.
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['DO_NOT_TRACK'] = '1'

setup_logger(log_level=args.verbose, use_stdout=args.log_stdout)

def apply_custom_paths():
    # extra model paths
    extra_model_paths_config_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "extra_model_paths.yaml")
    if os.path.isfile(extra_model_paths_config_path):
        utils.extra_config.load_extra_path_config(extra_model_paths_config_path)

    if args.extra_model_paths_config:
        for config_path in itertools.chain(*args.extra_model_paths_config):
            utils.extra_config.load_extra_path_config(config_path)

    # --output-directory, --input-directory, --user-directory
    if args.output_directory:
        output_dir = os.path.abspath(args.output_directory)
        logging.info(f"Setting output directory to: {output_dir}")
        folder_paths.set_output_directory(output_dir)

    # These are the default folders that checkpoints, clip and vae models will be saved to when using CheckpointSave, etc.. nodes
    folder_paths.add_model_folder_path("checkpoints", os.path.join(folder_paths.get_output_directory(), "checkpoints"))
    folder_paths.add_model_folder_path("clip", os.path.join(folder_paths.get_output_directory(), "clip"))
    folder_paths.add_model_folder_path("vae", os.path.join(folder_paths.get_output_directory(), "vae"))
    folder_paths.add_model_folder_path("diffusion_models",
                                       os.path.join(folder_paths.get_output_directory(), "diffusion_models"))
    folder_paths.add_model_folder_path("loras", os.path.join(folder_paths.get_output_directory(), "loras"))

    if args.input_directory:
        input_dir = os.path.abspath(args.input_directory)
        logging.info(f"Setting input directory to: {input_dir}")
        folder_paths.set_input_directory(input_dir)

    if args.user_directory:
        user_dir = os.path.abspath(args.user_directory)
        logging.info(f"Setting user directory to: {user_dir}")
        folder_paths.set_user_directory(user_dir)


def execute_prestartup_script():
    if args.disable_all_custom_nodes and len(args.whitelist_custom_nodes) == 0:
        return

    def execute_script(script_path):
        module_name = os.path.splitext(script_path)[0]
        try:
            spec = importlib.util.spec_from_file_location(module_name, script_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return True
        except Exception as e:
            logging.error(f"Failed to execute startup-script: {script_path} / {e}")
        return False

    node_paths = folder_paths.get_folder_paths("custom_nodes")
    for custom_node_path in node_paths:
        possible_modules = os.listdir(custom_node_path)
        node_prestartup_times = []

        for possible_module in possible_modules:
            module_path = os.path.join(custom_node_path, possible_module)
            if os.path.isfile(module_path) or module_path.endswith(".disabled") or module_path == "__pycache__":
                continue

            script_path = os.path.join(module_path, "prestartup_script.py")
            if os.path.exists(script_path):
                if args.disable_all_custom_nodes and possible_module not in args.whitelist_custom_nodes:
                    logging.info(f"Prestartup Skipping {possible_module} due to disable_all_custom_nodes and whitelist_custom_nodes")
                    continue
                time_before = time.perf_counter()
                success = execute_script(script_path)
                node_prestartup_times.append((time.perf_counter() - time_before, module_path, success))
    if len(node_prestartup_times) > 0:
        logging.info("\nPrestartup times for custom nodes:")
        for n in sorted(node_prestartup_times):
            if n[2]:
                import_message = ""
            else:
                import_message = " (PRESTARTUP FAILED)"
            logging.info("{:6.1f} seconds{}: {}".format(n[0], import_message, n[1]))
        logging.info("")

apply_custom_paths()
execute_prestartup_script()


# Main code
import asyncio
import shutil
import threading
import gc
from collections import deque
import torch

# ============ 多 GPU 调度配置 ============
ENABLE_MULTI_GPU = os.getenv('COMFY_MULTI_GPU_SCHED', '0') == '1'
NUM_GPUS = int(os.getenv('COMFY_NUM_GPUS', '4')) if ENABLE_MULTI_GPU else 1

if ENABLE_MULTI_GPU:
    logging.info(f"🚀 Multi-GPU mode ENABLED with {NUM_GPUS} GPUs")
else:
    logging.info("ℹ️  Single-GPU mode (default)")


if os.name == "nt":
    os.environ['MIMALLOC_PURGE_DELAY'] = '0'

if __name__ == "__main__":
    os.environ['TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL'] = '1'
    if args.default_device is not None:
        default_dev = args.default_device
        devices = list(range(32))
        devices.remove(default_dev)
        devices.insert(0, default_dev)
        devices = ','.join(map(str, devices))
        os.environ['CUDA_VISIBLE_DEVICES'] = str(devices)
        os.environ['HIP_VISIBLE_DEVICES'] = str(devices)

    if args.cuda_device is not None:
        os.environ['CUDA_VISIBLE_DEVICES'] = str(args.cuda_device)
        os.environ['HIP_VISIBLE_DEVICES'] = str(args.cuda_device)
        os.environ["ASCEND_RT_VISIBLE_DEVICES"] = str(args.cuda_device)
        logging.info("Set cuda device to: {}".format(args.cuda_device))

    if args.oneapi_device_selector is not None:
        os.environ['ONEAPI_DEVICE_SELECTOR'] = args.oneapi_device_selector
        logging.info("Set oneapi device selector to: {}".format(args.oneapi_device_selector))

    if args.deterministic:
        if 'CUBLAS_WORKSPACE_CONFIG' not in os.environ:
            os.environ['CUBLAS_WORKSPACE_CONFIG'] = ":4096:8"

    import cuda_malloc

if 'torch' in sys.modules:
    logging.warning("WARNING: Potential Error in code: Torch already imported, torch should never be imported before this point.")

import comfy.utils

import execution
import server
from protocol import BinaryEventTypes
import nodes
import comfy.model_management
import comfyui_version
import app.logger
import hook_breaker_ac10a0

def cuda_malloc_warning():
    device = comfy.model_management.get_torch_device()
    device_name = comfy.model_management.get_torch_device_name(device)
    cuda_malloc_warning = False
    if "cudaMallocAsync" in device_name:
        for b in cuda_malloc.blacklist:
            if b in device_name:
                cuda_malloc_warning = True
        if cuda_malloc_warning:
            logging.warning("\nWARNING: this card most likely does not support cuda-malloc, if you get \"CUDA error\" please run ComfyUI with: --disable-cuda-malloc\n")


def prompt_worker(q, server_instance):
    current_time: float = 0.0
    cache_type = execution.CacheType.CLASSIC
    if args.cache_lru > 0:
        cache_type = execution.CacheType.LRU
    elif args.cache_ram > 0:
        cache_type = execution.CacheType.RAM_PRESSURE
    elif args.cache_none:
        cache_type = execution.CacheType.NONE

    e = execution.PromptExecutor(server_instance, cache_type=cache_type, cache_args={ "lru" : args.cache_lru, "ram" : args.cache_ram } )
    last_gc_collect = 0
    need_gc = False
    gc_collect_interval = 10.0

    while True:
        timeout = 1000.0
        if need_gc:
            timeout = max(gc_collect_interval - (current_time - last_gc_collect), 0.0)

        queue_item = q.get(timeout=timeout)
        if queue_item is not None:
            item, item_id = queue_item
            execution_start_time = time.perf_counter()
            prompt_id = item[1]
            server_instance.last_prompt_id = prompt_id

            sensitive = item[5]
            extra_data = item[3].copy()
            for k in sensitive:
                extra_data[k] = sensitive[k]

            e.execute(item[2], prompt_id, extra_data, item[4])
            need_gc = True

            remove_sensitive = lambda prompt: prompt[:5] + prompt[6:]
            q.task_done(item_id,
                        e.history_result,
                        status=execution.PromptQueue.ExecutionStatus(
                            status_str='success' if e.success else 'error',
                            completed=e.success,
                            messages=e.status_messages), process_item=remove_sensitive)
            if server_instance.client_id is not None:
                server_instance.send_sync("executing", {"node": None, "prompt_id": prompt_id}, server_instance.client_id)

            current_time = time.perf_counter()
            execution_time = current_time - execution_start_time

            # Log Time in a more readable way after 10 minutes
            if execution_time > 600:
                execution_time = time.strftime("%H:%M:%S", time.gmtime(execution_time))
                logging.info(f"Prompt executed in {execution_time}")
            else:
                logging.info("Prompt executed in {:.2f} seconds".format(execution_time))

        flags = q.get_flags()
        free_memory = flags.get("free_memory", False)

        if flags.get("unload_models", free_memory):
            comfy.model_management.unload_all_models()
            need_gc = True
            last_gc_collect = 0

        if free_memory:
            e.reset()
            need_gc = True
            last_gc_collect = 0

        if need_gc:
            current_time = time.perf_counter()
            if (current_time - last_gc_collect) > gc_collect_interval:
                gc.collect()
                comfy.model_management.soft_empty_cache()
                last_gc_collect = current_time
                need_gc = False
                hook_breaker_ac10a0.restore_functions()


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
                    comfy.model_management.soft_empty_cache(device=device)
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
                comfy.model_management.soft_empty_cache(device=device)
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


async def run(server_instance, address='', port=8188, verbose=True, call_on_start=None):
    addresses = []
    for addr in address.split(","):
        addresses.append((addr, port))
    await asyncio.gather(
        server_instance.start_multi_address(addresses, call_on_start, verbose), server_instance.publish_loop()
    )

def hijack_progress(server_instance):
    def hook(value, total, preview_image, prompt_id=None, node_id=None):
        executing_context = get_executing_context()
        if prompt_id is None and executing_context is not None:
            prompt_id = executing_context.prompt_id
        if node_id is None and executing_context is not None:
            node_id = executing_context.node_id
        comfy.model_management.throw_exception_if_processing_interrupted()
        if prompt_id is None:
            prompt_id = server_instance.last_prompt_id
        if node_id is None:
            node_id = server_instance.last_node_id
        progress = {"value": value, "max": total, "prompt_id": prompt_id, "node": node_id}
        get_progress_state().update_progress(node_id, value, total, preview_image)

        server_instance.send_sync("progress", progress, server_instance.client_id)
        if preview_image is not None:
            # Only send old method if client doesn't support preview metadata
            if not feature_flags.supports_feature(
                server_instance.sockets_metadata,
                server_instance.client_id,
                "supports_preview_metadata",
            ):
                server_instance.send_sync(
                    BinaryEventTypes.UNENCODED_PREVIEW_IMAGE,
                    preview_image,
                    server_instance.client_id,
                )

    comfy.utils.set_progress_bar_global_hook(hook)


def cleanup_temp():
    temp_dir = folder_paths.get_temp_directory()
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)


def setup_database():
    try:
        from app.database.db import init_db, dependencies_available
        if dependencies_available():
            init_db()
    except Exception as e:
        logging.error(f"Failed to initialize database. Please ensure you have installed the latest requirements. If the error persists, please report this as in future the database will be required: {e}")


def start_comfyui(asyncio_loop=None):
    """
    Starts the ComfyUI server using the provided asyncio event loop or creates a new one.
    Returns the event loop, server instance, and a function to start the server asynchronously.
    """
    if args.temp_directory:
        temp_dir = os.path.join(os.path.abspath(args.temp_directory), "temp")
        logging.info(f"Setting temp directory to: {temp_dir}")
        folder_paths.set_temp_directory(temp_dir)
    cleanup_temp()

    if args.windows_standalone_build:
        try:
            import new_updater
            new_updater.update_windows_updater()
        except:
            pass

    if not asyncio_loop:
        asyncio_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(asyncio_loop)
    prompt_server = server.PromptServer(asyncio_loop)

    hook_breaker_ac10a0.save_functions()
    asyncio_loop.run_until_complete(nodes.init_extra_nodes(
        init_custom_nodes=(not args.disable_all_custom_nodes) or len(args.whitelist_custom_nodes) > 0,
        init_api_nodes=not args.disable_api_nodes
    ))
    hook_breaker_ac10a0.restore_functions()

    cuda_malloc_warning()
    setup_database()

    prompt_server.add_routes()
    hijack_progress(prompt_server)

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

    if args.quick_test_for_ci:
        exit(0)

    os.makedirs(folder_paths.get_temp_directory(), exist_ok=True)
    call_on_start = None
    if args.auto_launch:
        def startup_server(scheme, address, port):
            import webbrowser
            if os.name == 'nt' and address == '0.0.0.0':
                address = '127.0.0.1'
            if ':' in address:
                address = "[{}]".format(address)
            webbrowser.open(f"{scheme}://{address}:{port}")
        call_on_start = startup_server

    async def start_all():
        await prompt_server.setup()
        await run(prompt_server, address=args.listen, port=args.port, verbose=not args.dont_print_server, call_on_start=call_on_start)

    # Returning these so that other code can integrate with the ComfyUI loop and server
    return asyncio_loop, prompt_server, start_all


if __name__ == "__main__":
    # Running directly, just start ComfyUI.
    logging.info("Python version: {}".format(sys.version))
    logging.info("ComfyUI version: {}".format(comfyui_version.__version__))

    if sys.version_info.major == 3 and sys.version_info.minor < 10:
        logging.warning("WARNING: You are using a python version older than 3.10, please upgrade to a newer one. 3.12 and above is recommended.")

    event_loop, _, start_all_func = start_comfyui()
    try:
        x = start_all_func()
        app.logger.print_startup_warnings()
        event_loop.run_until_complete(x)
    except KeyboardInterrupt:
        logging.info("\nStopped server")

    cleanup_temp()
