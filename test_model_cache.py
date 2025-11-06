#!/usr/bin/env python3
"""
测试模型缓存功能
验证同一模型只加载一次到 CPU RAM
"""

import os
import sys
import time
import psutil
import logging

# 添加 ComfyUI 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import comfy.utils

# 设置日志级别
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_memory_usage_gb():
    """获取当前进程的内存占用（GB）"""
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / 1024**3

def test_cache():
    """测试缓存功能"""

    print("=" * 60)
    print("测试模型缓存功能")
    print("=" * 60)

    # 查找一个测试模型文件
    model_dirs = [
        "models/checkpoints",
        "models/unet",
        "models/clip",
    ]

    test_model = None
    for model_dir in model_dirs:
        if os.path.exists(model_dir):
            files = [f for f in os.listdir(model_dir) if f.endswith(('.safetensors', '.ckpt', '.pt'))]
            if files:
                test_model = os.path.join(model_dir, files[0])
                break

    if not test_model:
        print("❌ 错误: 找不到测试模型文件")
        print("请确保 models/checkpoints 或 models/unet 目录中有模型文件")
        return

    print(f"\n📁 测试模型: {test_model}")
    file_size = os.path.getsize(test_model) / 1024**3
    print(f"📦 文件大小: {file_size:.2f} GB")
    print()

    # 测试 1: 首次加载
    print("=" * 60)
    print("测试 1: 首次加载模型")
    print("=" * 60)

    mem_before = get_memory_usage_gb()
    print(f"加载前内存: {mem_before:.2f} GB")

    start_time = time.time()
    sd1 = comfy.utils.load_torch_file_cached(test_model)
    load_time_1 = time.time() - start_time

    mem_after_1 = get_memory_usage_gb()
    mem_increase_1 = mem_after_1 - mem_before

    print(f"加载后内存: {mem_after_1:.2f} GB")
    print(f"内存增加: {mem_increase_1:.2f} GB")
    print(f"加载时间: {load_time_1:.2f} 秒")
    print(f"状态: ✅ 首次加载完成（预期从磁盘读取）")
    print()

    # 测试 2: 第二次加载（应该使用缓存）
    print("=" * 60)
    print("测试 2: 第二次加载同一模型（应该使用缓存）")
    print("=" * 60)

    mem_before_2 = get_memory_usage_gb()
    print(f"加载前内存: {mem_before_2:.2f} GB")

    start_time = time.time()
    sd2 = comfy.utils.load_torch_file_cached(test_model)
    load_time_2 = time.time() - start_time

    mem_after_2 = get_memory_usage_gb()
    mem_increase_2 = mem_after_2 - mem_before_2

    print(f"加载后内存: {mem_after_2:.2f} GB")
    print(f"内存增加: {mem_increase_2:.2f} GB")
    print(f"加载时间: {load_time_2:.2f} 秒")

    # 验证是否使用了缓存
    if sd1 is sd2:
        print(f"状态: ✅ 使用缓存成功（两次加载返回同一对象）")
    else:
        print(f"状态: ⚠️  警告：两次加载返回不同对象")
    print()

    # 测试 3: 第三次加载
    print("=" * 60)
    print("测试 3: 第三次加载（模拟多 GPU）")
    print("=" * 60)

    start_time = time.time()
    sd3 = comfy.utils.load_torch_file_cached(test_model)
    load_time_3 = time.time() - start_time

    mem_after_3 = get_memory_usage_gb()
    mem_increase_3 = mem_after_3 - mem_before_2

    print(f"加载后内存: {mem_after_3:.2f} GB")
    print(f"总内存增加: {mem_increase_3:.2f} GB")
    print(f"加载时间: {load_time_3:.2f} 秒")
    print()

    # 测试 4: 第四次加载
    print("=" * 60)
    print("测试 4: 第四次加载（模拟 4 GPU）")
    print("=" * 60)

    start_time = time.time()
    sd4 = comfy.utils.load_torch_file_cached(test_model)
    load_time_4 = time.time() - start_time

    mem_after_4 = get_memory_usage_gb()
    mem_total_increase = mem_after_4 - mem_before

    print(f"加载后内存: {mem_after_4:.2f} GB")
    print(f"总内存增加: {mem_total_increase:.2f} GB")
    print(f"加载时间: {load_time_4:.2f} 秒")
    print()

    # 总结
    print("=" * 60)
    print("测试总结")
    print("=" * 60)
    print(f"模型文件大小: {file_size:.2f} GB")
    print(f"CPU RAM 总增加: {mem_total_increase:.2f} GB")
    print()
    print(f"首次加载时间: {load_time_1:.2f} 秒（磁盘读取）")
    print(f"第二次加载时间: {load_time_2:.4f} 秒（缓存）")
    print(f"第三次加载时间: {load_time_3:.4f} 秒（缓存）")
    print(f"第四次加载时间: {load_time_4:.4f} 秒（缓存）")
    print()

    if load_time_2 < load_time_1 / 10:  # 缓存应该快至少 10 倍
        print(f"✅ 速度提升: {load_time_1/load_time_2:.0f}x")
    else:
        print(f"⚠️  警告: 缓存速度提升不明显")

    print()
    expected_mem = file_size * 1.2  # 考虑一些开销
    if mem_total_increase < expected_mem * 1.5:
        print(f"✅ 内存效率: 4 次加载只占用约 1 份模型大小的内存")
        print(f"   理论: {file_size:.2f} GB × 4 = {file_size * 4:.2f} GB")
        print(f"   实际: {mem_total_increase:.2f} GB")
        print(f"   节省: {(1 - mem_total_increase / (file_size * 4)) * 100:.1f}%")
    else:
        print(f"⚠️  警告: 内存占用超出预期")

    print()

    # 验证缓存状态
    if hasattr(comfy.utils, '_state_dict_cache'):
        cache_size = len(comfy.utils._state_dict_cache)
        print(f"📊 缓存状态:")
        print(f"   缓存条目数: {cache_size}")
        print(f"   缓存的文件: {list(comfy.utils._state_dict_cache.keys())}")

    print()
    print("=" * 60)
    print("测试完成！")
    print("=" * 60)

if __name__ == "__main__":
    try:
        test_cache()
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
