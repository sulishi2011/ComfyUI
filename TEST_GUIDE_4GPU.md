# ComfyUI 多 GPU 测试指南（4 卡服务器）

**测试日期**: 2025-11-05
**目标**: 在实际 4 卡服务器上验证多 GPU 架构

---

## 📋 测试前准备

### 1. 服务器硬件要求

```bash
# 检查 GPU 数量
nvidia-smi --list-gpus
# 应该显示 4 张 GPU

# 检查 GPU 详细信息
nvidia-smi

# 检查内存
free -h
# 建议 64GB+

# 检查 CUDA
python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}, GPUs: {torch.cuda.device_count()}')"
# 应该显示: CUDA: True, GPUs: 4
```

### 2. 确保 ComfyUI 单 GPU 模式正常

```bash
# 先测试原有单 GPU 模式是否正常
export COMFY_MULTI_GPU_SCHED=0
python main.py --listen 0.0.0.0 --port 8188

# 在另一个终端测试
curl http://localhost:8188
# 应该返回 ComfyUI 前端页面
```

按 `Ctrl+C` 停止，确认单 GPU 模式没问题。

---

## 🚀 阶段 1: 启动多 GPU 模式（不使用 Nginx）

### 步骤 1.1: 启动 ComfyUI

```bash
# 启动多 GPU 模式
./start_multi_gpu.sh
```

### 步骤 1.2: 检查启动日志

**应该看到以下关键日志**：

```
✅ Multi-GPU scheduling ENABLED
🚀 Multi-GPU mode ENABLED with 4 GPUs
📋 Created 4 task queues for multi-GPU scheduling
🚀 Started worker thread for GPU 0
🚀 Started worker thread for GPU 1
🚀 Started worker thread for GPU 2
🚀 Started worker thread for GPU 3
🔥 [GPU 0] Starting warmup...
✅ [GPU 0] Warmup completed
🔥 [GPU 1] Starting warmup...
✅ [GPU 1] Warmup completed
🔥 [GPU 2] Starting warmup...
✅ [GPU 2] Warmup completed
🔥 [GPU 3] Starting warmup...
✅ [GPU 3] Warmup completed
🔍 Checking custom nodes compatibility...
✅ No obvious device hardcoding detected
```

**⚠️ 如果看到错误**：
- 检查 GPU 数量是否为 4
- 检查 CUDA 是否可用
- 查看完整错误信息

### 步骤 1.3: 验证基本功能（直接访问后端）

```bash
# 在另一个终端

# 测试 1: 检查服务是否运行
curl http://localhost:8188
# 应该返回前端页面

# 测试 2: 检查队列汇总
curl http://localhost:8188/queue/all | jq
# 应该返回 4 个队列的状态
```

**预期输出**：
```json
{
  "queues": [
    {"gpu_id": 0, "queue_running": [], "queue_pending": [], "running_count": 0, "pending_count": 0},
    {"gpu_id": 1, "queue_running": [], "queue_pending": [], "running_count": 0, "pending_count": 0},
    {"gpu_id": 2, "queue_running": [], "queue_pending": [], "running_count": 0, "pending_count": 0},
    {"gpu_id": 3, "queue_running": [], "queue_pending": [], "running_count": 0, "pending_count": 0}
  ],
  "total_running": 0,
  "total_pending": 0
}
```

---

## 🧪 阶段 2: 测试任务提交（手动指定 GPU）

### 步骤 2.1: 准备测试 workflow

创建简单的测试 workflow（`test_workflow.json`）：

```json
{
  "3": {
    "inputs": {
      "seed": 123456,
      "steps": 20,
      "cfg": 8.0,
      "sampler_name": "euler",
      "scheduler": "normal",
      "denoise": 1.0,
      "model": ["4", 0],
      "positive": ["6", 0],
      "negative": ["7", 0],
      "latent_image": ["5", 0]
    },
    "class_type": "KSampler"
  },
  "4": {
    "inputs": {
      "ckpt_name": "你的模型名称.safetensors"
    },
    "class_type": "CheckpointLoaderSimple"
  },
  "5": {
    "inputs": {
      "width": 512,
      "height": 512,
      "batch_size": 1
    },
    "class_type": "EmptyLatentImage"
  },
  "6": {
    "inputs": {
      "text": "beautiful landscape, high quality",
      "clip": ["4", 1]
    },
    "class_type": "CLIPTextEncode"
  },
  "7": {
    "inputs": {
      "text": "bad quality, blurry",
      "clip": ["4", 1]
    },
    "class_type": "CLIPTextEncode"
  },
  "8": {
    "inputs": {
      "samples": ["3", 0],
      "vae": ["4", 2]
    },
    "class_type": "VAEDecode"
  },
  "9": {
    "inputs": {
      "filename_prefix": "ComfyUI",
      "images": ["8", 0]
    },
    "class_type": "SaveImage"
  }
}
```

**⚠️ 重要**: 修改 `"ckpt_name"` 为你服务器上实际存在的模型文件名。

### 步骤 2.2: 向不同 GPU 提交任务

```bash
# 提交到 GPU 0（通过 header）
curl -X POST http://localhost:8188/prompt \
  -H "Content-Type: application/json" \
  -H "X-TARGET-GPU: 0" \
  -d @test_workflow.json

# 提交到 GPU 1
curl -X POST http://localhost:8188/prompt \
  -H "Content-Type: application/json" \
  -H "X-TARGET-GPU: 1" \
  -d @test_workflow.json

# 提交到 GPU 2
curl -X POST http://localhost:8188/prompt \
  -H "Content-Type: application/json" \
  -H "X-TARGET-GPU: 2" \
  -d @test_workflow.json

# 提交到 GPU 3
curl -X POST http://localhost:8188/prompt \
  -H "Content-Type: application/json" \
  -H "X-TARGET-GPU: 3" \
  -d @test_workflow.json
```

### 步骤 2.3: 监控执行

**终端 1 - 查看日志**：
```bash
tail -f logs/comfyui_*.log
```

**应该看到**：
```
Routing prompt abc123 to GPU 0
🔧 [GPU 0] Worker started on cuda:0
✅ [GPU 0] Prompt abc123 executed in 15.32s

Routing prompt def456 to GPU 1
🔧 [GPU 1] Worker started on cuda:1
✅ [GPU 1] Prompt def456 executed in 15.18s

...
```

**终端 2 - 监控 GPU**：
```bash
watch -n 1 nvidia-smi
```

**应该看到**：
- 4 张 GPU 都有负载
- 每张 GPU 的 VRAM 使用量相近
- 进程名称都是 `python main.py`（同一个进程）

**终端 3 - 查询队列状态**：
```bash
watch -n 2 "curl -s http://localhost:8188/queue/all | jq '.queues[] | {gpu: .gpu_id, running: .running_count, pending: .pending_count}'"
```

---

## 🎯 阶段 3: 并发压力测试

### 步骤 3.1: 批量并发提交

```bash
# 创建并发测试脚本
cat > test_concurrent.sh << 'EOF'
#!/bin/bash

echo "🚀 Starting concurrent test..."

# 向每个 GPU 提交 10 个任务
for gpu in 0 1 2 3; do
  echo "Submitting 10 tasks to GPU $gpu"
  for i in {1..10}; do
    curl -X POST http://localhost:8188/prompt \
      -H "Content-Type: application/json" \
      -H "X-TARGET-GPU: $gpu" \
      -d @test_workflow.json \
      -s > /dev/null &
  done
done

wait

echo "✅ All tasks submitted!"
echo "Check queue status:"
curl -s http://localhost:8188/queue/all | jq
EOF

chmod +x test_concurrent.sh
./test_concurrent.sh
```

### 步骤 3.2: 监控指标

```bash
# 查看每个 GPU 的性能指标
tail -f logs/comfyui_*.log | grep "📊"
```

**预期输出**：
```
📊 [GPU 0] tasks=10, success=100.0%, queue=2.5, wait=150ms, exec=15234ms, oom=0
📊 [GPU 1] tasks=10, success=100.0%, queue=2.3, wait=142ms, exec=15180ms, oom=0
📊 [GPU 2] tasks=10, success=100.0%, queue=2.6, wait=158ms, exec=15298ms, oom=0
📊 [GPU 3] tasks=10, success=100.0%, queue=2.4, wait=145ms, exec=15210ms, oom=0
```

### 步骤 3.3: 验证性能提升

```bash
# 创建性能对比测试
cat > benchmark.sh << 'EOF'
#!/bin/bash

echo "=== 性能测试 ==="

# 测试 1: 单 GPU 模式（10 个任务串行）
echo "测试 1: 单 GPU 模式"
start=$(date +%s)
for i in {1..10}; do
  curl -X POST http://localhost:8188/prompt \
    -H "Content-Type: application/json" \
    -H "X-TARGET-GPU: 0" \
    -d @test_workflow.json -s > /dev/null
done
end=$(date +%s)
single_time=$((end - start))
echo "单 GPU 完成 10 个任务耗时: ${single_time}s"

# 等待队列清空
sleep 5

# 测试 2: 4 GPU 并行（40 个任务并行）
echo "测试 2: 4 GPU 并行"
start=$(date +%s)
for gpu in 0 1 2 3; do
  for i in {1..10}; do
    curl -X POST http://localhost:8188/prompt \
      -H "Content-Type: application/json" \
      -H "X-TARGET-GPU: $gpu" \
      -d @test_workflow.json -s > /dev/null &
  done
done
wait
end=$(date +%s)
multi_time=$((end - start))
echo "4 GPU 并行完成 40 个任务耗时: ${multi_time}s"

# 计算加速比
speedup=$(echo "scale=2; ($single_time * 4) / $multi_time" | bc)
echo "性能提升: ${speedup}x"
EOF

chmod +x benchmark.sh
./benchmark.sh
```

**预期结果**：
```
测试 1: 单 GPU 完成 10 个任务耗时: 180s
测试 2: 4 GPU 并行完成 40 个任务耗时: 200s
性能提升: 3.60x
```

---

## 🌐 阶段 4: 配置 Nginx（生产环境）

### 步骤 4.1: 安装 Nginx

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install nginx

# CentOS/RHEL
sudo yum install nginx

# 检查安装
nginx -v
```

### 步骤 4.2: 配置 Nginx

```bash
# 复制配置文件
sudo cp nginx.conf /etc/nginx/sites-available/comfyui-multi-gpu

# 创建软链接
sudo ln -s /etc/nginx/sites-available/comfyui-multi-gpu /etc/nginx/sites-enabled/

# 测试配置
sudo nginx -t
```

**预期输出**：
```
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

### 步骤 4.3: 启动 Nginx

```bash
# 启动 Nginx
sudo systemctl start nginx

# 设置开机自启
sudo systemctl enable nginx

# 检查状态
sudo systemctl status nginx
```

### 步骤 4.4: 测试 Nginx 代理

```bash
# 测试 4 个端口
for port in 8181 8182 8183 8184; do
  echo "Testing port $port..."
  curl -s http://localhost:$port/queue | jq .gpu_id
done
```

**预期输出**：
```
Testing port 8181...
0
Testing port 8182...
1
Testing port 8183...
2
Testing port 8184...
3
```

### 步骤 4.5: 通过 Nginx 提交任务

```bash
# 每个端口自动路由到对应 GPU
curl -X POST http://localhost:8181/prompt -d @test_workflow.json  # → GPU 0
curl -X POST http://localhost:8182/prompt -d @test_workflow.json  # → GPU 1
curl -X POST http://localhost:8183/prompt -d @test_workflow.json  # → GPU 2
curl -X POST http://localhost:8184/prompt -d @test_workflow.json  # → GPU 3
```

---

## 📊 阶段 5: 验证核心特性

### 测试 5.1: RAM 共享验证

```bash
# 记录 ComfyUI 进程的内存使用
PID=$(pgrep -f "python main.py")
echo "ComfyUI PID: $PID"

# 提交任务前的内存
RSS_BEFORE=$(ps -o rss= -p $PID)
echo "任务前 RSS: $((RSS_BEFORE / 1024))MB"

# 向 4 个 GPU 提交相同模型的任务
for gpu in 0 1 2 3; do
  curl -X POST http://localhost:8188/prompt \
    -H "X-TARGET-GPU: $gpu" \
    -d @test_workflow.json -s > /dev/null &
done
wait

# 等待任务完成
sleep 30

# 提交任务后的内存
RSS_AFTER=$(ps -o rss= -p $PID)
echo "任务后 RSS: $((RSS_AFTER / 1024))MB"

# 计算增长
GROWTH=$((RSS_AFTER - RSS_BEFORE))
echo "内存增长: $((GROWTH / 1024))MB"
echo "增长率: $(echo "scale=2; ($GROWTH * 100) / $RSS_BEFORE" | bc)%"
```

**预期结果**：
- 内存增长 < 10%（模型只加载一次）
- 如果是多进程模式，内存会增长 300%+

### 测试 5.2: 模型加载速度

```bash
# 创建模型切换测试
cat > test_model_loading.sh << 'EOF'
#!/bin/bash

# 修改 workflow 使用不同模型
# 测试从 RAM 加载的速度

echo "测试模型加载速度..."

# 提交第一个任务（首次加载，从硬盘）
echo "首次加载（从硬盘到 RAM）..."
time curl -X POST http://localhost:8188/prompt \
  -H "X-TARGET-GPU: 0" \
  -d @test_workflow.json -s > /dev/null

# 等待完成
sleep 20

# 提交第二个任务到另一个 GPU（从 RAM 加载）
echo "再次加载（从 RAM 到 VRAM）..."
time curl -X POST http://localhost:8188/prompt \
  -H "X-TARGET-GPU: 1" \
  -d @test_workflow.json -s > /dev/null
EOF

chmod +x test_model_loading.sh
./test_model_loading.sh
```

**预期结果**：
- 首次加载：5-10 秒
- 后续加载：1-2 秒（快 3-5 倍）

### 测试 5.3: 队列隔离

```bash
# 向 GPU 0 提交大量任务
for i in {1..20}; do
  curl -X POST http://localhost:8181/prompt -d @test_workflow.json -s > /dev/null &
done

# 立即查询所有队列
curl -s http://localhost:8188/queue/all | jq '.queues[] | {gpu: .gpu_id, pending: .pending_count}'
```

**预期输出**：
```json
{"gpu": 0, "pending": 20}
{"gpu": 1, "pending": 0}
{"gpu": 2, "pending": 0}
{"gpu": 3, "pending": 0}
```

**验证**：只有 GPU 0 的队列有任务，其他 GPU 不受影响。

### 测试 5.4: OOM 恢复

```bash
# 提交一个超大批次任务（故意触发 OOM）
cat > test_oom.json << 'EOF'
{
  "5": {
    "inputs": {
      "width": 4096,
      "height": 4096,
      "batch_size": 16
    },
    "class_type": "EmptyLatentImage"
  }
}
EOF

curl -X POST http://localhost:8188/prompt \
  -H "X-TARGET-GPU: 0" \
  -d @test_oom.json

# 查看日志
tail -f logs/comfyui_*.log | grep -E "(OOM|clearing cache)"
```

**预期输出**：
```
💥 [GPU 0] OOM for prompt abc123, clearing cache
✅ [GPU 0] Cache cleared, retrying...
```

---

## ✅ 验收标准

### 必须通过的测试：

- [ ] **启动成功**：看到 4 个 worker 启动日志
- [ ] **GPU 识别**：4 张 GPU 都被识别和预热
- [ ] **任务路由**：任务能正确路由到指定 GPU
- [ ] **真并行**：4 张 GPU 同时执行（nvidia-smi 验证）
- [ ] **队列隔离**：各 GPU 队列独立，互不影响
- [ ] **RAM 共享**：内存增长 < 10%
- [ ] **性能提升**：吞吐量达到 3.5x 以上

### 性能指标：

```
单 GPU 基准:
- 单张图片生成: 15-20 秒
- 每小时吞吐量: 180-240 张

4 GPU 并行（目标）:
- 单张图片生成: 15-20 秒（不变）
- 每小时吞吐量: 630-960 张（3.5-4x）
- RAM 增长: < 10%
- 模型加载加速: 3-5x
```

---

## 🐛 故障排查

### 问题 1: 启动时只看到单 GPU 日志

**检查**：
```bash
echo $COMFY_MULTI_GPU_SCHED
echo $COMFY_NUM_GPUS
```

**解决**：
```bash
export COMFY_MULTI_GPU_SCHED=1
export COMFY_NUM_GPUS=4
```

### 问题 2: 所有任务都在 GPU 0

**检查 Nginx**：
```bash
sudo nginx -T | grep X-TARGET-GPU
```

**应该看到**：
```
proxy_set_header X-TARGET-GPU 0;
proxy_set_header X-TARGET-GPU 1;
...
```

### 问题 3: GPU 1/2/3 没有负载

**查看日志**：
```bash
grep "Worker started" logs/comfyui_*.log
```

**应该看到**：
```
🔧 [GPU 0] Worker started on cuda:0
🔧 [GPU 1] Worker started on cuda:1
🔧 [GPU 2] Worker started on cuda:2
🔧 [GPU 3] Worker started on cuda:3
```

**如果缺失**，检查：
```bash
python -c "import torch; print(torch.cuda.device_count())"
# 必须返回 4
```

### 问题 4: OOM 崩溃

**增加错误恢复**：
- 检查日志中的 OOM 计数
- 如果频繁 OOM，减小 batch size
- 或者使用更小的模型

### 问题 5: 性能没有提升

**可能原因**：
1. 模型太小，GPU 未充分利用
2. 硬盘 I/O 瓶颈（使用 NVMe SSD）
3. CPU 瓶颈（增加 CPU 核心）

**测试**：
```bash
# 使用更大的模型和批次
# 确保 GPU 利用率 > 80%
```

---

## 📝 测试报告模板

完成测试后，填写以下报告：

```markdown
# ComfyUI 多 GPU 测试报告

## 环境信息
- GPU 型号: _______
- GPU 数量: 4
- VRAM: _______/卡
- RAM: _______
- CUDA 版本: _______
- ComfyUI 版本: v0.3.68

## 测试结果

### 基础功能
- [ ] 启动成功
- [ ] 4 GPU 识别
- [ ] 任务路由正常
- [ ] 队列隔离正常

### 性能测试
- 单 GPU 吞吐量: _______ 张/小时
- 4 GPU 吞吐量: _______ 张/小时
- 加速比: _______x
- RAM 增长率: _______%
- 模型加载加速: _______x

### 稳定性
- 连续运行时间: _______ 小时
- OOM 次数: _______
- 任务成功率: _______%

### 问题记录
（如有问题，详细描述）

### 结论
□ 通过测试，可以投入生产使用
□ 需要进一步优化
□ 存在严重问题，需要修复
```

---

## 🎉 测试成功后

### 生产部署清单：

- [ ] 配置 systemd 服务（开机自启）
- [ ] 配置日志轮转
- [ ] 设置监控告警
- [ ] 配置防火墙规则
- [ ] 文档化部署流程
- [ ] 培训运维团队

### systemd 服务配置：

```bash
sudo nano /etc/systemd/system/comfyui-multi-gpu.service
```

```ini
[Unit]
Description=ComfyUI Multi-GPU Service
After=network.target

[Service]
Type=simple
User=你的用户名
WorkingDirectory=/path/to/ComfyUI
Environment="CUDA_VISIBLE_DEVICES=0,1,2,3"
Environment="COMFY_MULTI_GPU_SCHED=1"
Environment="COMFY_NUM_GPUS=4"
ExecStart=/usr/bin/python3 main.py --listen 0.0.0.0 --port 8188
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable comfyui-multi-gpu
sudo systemctl start comfyui-multi-gpu
```

---

祝测试顺利！🚀
