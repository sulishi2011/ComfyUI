# ComfyUI 多 GPU 使用示例

**核心概念**: 每个端口（8181-8184）都是一个完整的 ComfyUI 实例

---

## 🎯 核心理念

将 `http://localhost:8181`、`http://localhost:8182` 等**每个端口**都当作独立的 ComfyUI 服务器使用：

- ✅ 所有 API 接口都可以访问
- ✅ 文件上传/下载正常工作
- ✅ WebSocket 连接正常工作
- ✅ 队列查询返回对应 GPU 的状态
- ⚡ 唯一区别：请求被路由到不同的 GPU

---

## 📋 完整的 API 兼容性

| 接口 | 8181 (GPU 0) | 8182 (GPU 1) | 8183 (GPU 2) | 8184 (GPU 3) |
|------|--------------|--------------|--------------|--------------|
| `POST /prompt` | ✅ → GPU 0 | ✅ → GPU 1 | ✅ → GPU 2 | ✅ → GPU 3 |
| `GET /queue` | ✅ | ✅ | ✅ | ✅ |
| `POST /queue` | ✅ | ✅ | ✅ | ✅ |
| `POST /interrupt` | ✅ | ✅ | ✅ | ✅ |
| `GET /history` | ✅ | ✅ | ✅ | ✅ |
| `POST /upload/image` | ✅ | ✅ | ✅ | ✅ |
| `POST /upload/mask` | ✅ | ✅ | ✅ | ✅ |
| `GET /view` | ✅ | ✅ | ✅ | ✅ |
| `WebSocket /ws` | ✅ | ✅ | ✅ | ✅ |

---

## 🧪 实际使用示例

### 1. 图片上传

```bash
# 上传到 GPU 0
curl -X POST http://localhost:8181/upload/image \
  -F "image=@myimage.png" \
  -F "subfolder=test"

# 上传到 GPU 1
curl -X POST http://localhost:8182/upload/image \
  -F "image=@myimage.png" \
  -F "subfolder=test"
```

**说明**：
- 图片会上传到共享的 `input/` 文件夹
- 所有 GPU 都可以访问相同的图片
- 文件名相同也不会冲突（自动处理）

### 2. 提交生图任务

```bash
# 向 GPU 0 提交任务
curl -X POST http://localhost:8181/prompt \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": {
      "3": {
        "class_type": "KSampler",
        "inputs": {...}
      },
      ...
    }
  }'

# 向 GPU 1 提交任务
curl -X POST http://localhost:8182/prompt \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": {...}
  }'

# 向 GPU 2 提交任务
curl -X POST http://localhost:8183/prompt \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": {...}
  }'
```

**返回示例**：
```json
{
  "prompt_id": "abc123-def456-...",
  "number": 42,
  "gpu_id": 0,  // 自动添加，标识使用的 GPU
  "node_errors": []
}
```

### 3. 查询队列状态

```bash
# 查询 GPU 0 的队列
curl http://localhost:8181/queue

# 查询 GPU 1 的队列
curl http://localhost:8182/queue

# 查询所有队列汇总（直接访问后端）
curl http://localhost:8188/queue/all
```

**GPU 0 返回示例**：
```json
{
  "queue_running": [
    [42, "abc123-...", {...}, {...}, [...]]
  ],
  "queue_pending": [
    [43, "def456-...", {...}, {...}, [...]],
    [44, "ghi789-...", {...}, {...}, [...]]
  ],
  "gpu_id": 0
}
```

**所有队列汇总示例**：
```json
{
  "queues": [
    {
      "gpu_id": 0,
      "queue_running": [...],
      "queue_pending": [...],
      "running_count": 1,
      "pending_count": 2
    },
    {
      "gpu_id": 1,
      "queue_running": [...],
      "queue_pending": [...],
      "running_count": 0,
      "pending_count": 5
    },
    ...
  ],
  "total_running": 2,
  "total_pending": 15
}
```

### 4. 清空队列

```bash
# 清空 GPU 0 的队列
curl -X POST http://localhost:8181/queue \
  -H "Content-Type: application/json" \
  -d '{"clear": true}'

# 清空 GPU 2 的队列
curl -X POST http://localhost:8183/queue \
  -H "Content-Type: application/json" \
  -d '{"clear": true}'
```

**说明**：只清空指定 GPU 的队列，其他 GPU 不受影响

### 5. 中断任务

```bash
# 中断特定任务（无论在哪个 GPU 上）
curl -X POST http://localhost:8188/interrupt \
  -H "Content-Type: application/json" \
  -d '{"prompt_id": "abc123-def456-..."}'

# 或者通过任意 GPU 端口中断
curl -X POST http://localhost:8181/interrupt \
  -H "Content-Type: application/json" \
  -d '{"prompt_id": "abc123-def456-..."}'
```

**说明**：系统会自动查找该任务在哪个 GPU 上执行

### 6. 查看图片

```bash
# 查看生成的图片（任意端口都可以）
curl http://localhost:8181/view?filename=ComfyUI_00001.png

# 或者
curl http://localhost:8182/view?filename=ComfyUI_00001.png
```

**说明**：
- 所有端口访问相同的 `output/` 文件夹
- 文件在所有端口都可见

### 7. WebSocket 连接

```javascript
// 连接到 GPU 0
const ws1 = new WebSocket('ws://localhost:8181/ws?clientId=xxx');

ws1.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('[GPU 0]', data);
};

// 连接到 GPU 1
const ws2 = new WebSocket('ws://localhost:8182/ws?clientId=yyy');

ws2.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('[GPU 1]', data);
};
```

**说明**：
- 每个 WebSocket 连接使用不同的 `clientId`
- 只接收对应 GPU 的进度更新

---

## 🎨 前端集成示例

### 场景 1: 用户选择 GPU

```javascript
class ComfyUIClient {
  constructor(gpuId) {
    this.gpuId = gpuId;
    this.port = 8181 + gpuId;
    this.baseUrl = `http://localhost:${this.port}`;
    this.ws = null;
  }

  connect() {
    this.ws = new WebSocket(`ws://localhost:${this.port}/ws?clientId=${this.clientId}`);
    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      this.handleMessage(data);
    };
  }

  async uploadImage(file) {
    const formData = new FormData();
    formData.append('image', file);

    const response = await fetch(`${this.baseUrl}/upload/image`, {
      method: 'POST',
      body: formData
    });

    return response.json();
  }

  async submitPrompt(prompt) {
    const response = await fetch(`${this.baseUrl}/prompt`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt })
    });

    return response.json();
  }

  async getQueue() {
    const response = await fetch(`${this.baseUrl}/queue`);
    return response.json();
  }
}

// 使用
const client = new ComfyUIClient(0); // GPU 0
client.connect();

// 上传图片
await client.uploadImage(imageFile);

// 提交任务
const result = await client.submitPrompt(workflow);
console.log(`Task submitted to GPU ${result.gpu_id}`);
```

### 场景 2: 自动负载均衡

```javascript
class LoadBalancedComfyUI {
  constructor() {
    this.gpuPorts = [8181, 8182, 8183, 8184];
    this.clients = this.gpuPorts.map((port, idx) => new ComfyUIClient(idx));
  }

  async selectBestGPU() {
    // 查询所有队列
    const response = await fetch('http://localhost:8188/queue/all');
    const data = await response.json();

    // 找到最空闲的 GPU
    let bestGPU = 0;
    let minLoad = Infinity;

    data.queues.forEach(q => {
      const load = q.running_count * 10 + q.pending_count;
      if (load < minLoad) {
        minLoad = load;
        bestGPU = q.gpu_id;
      }
    });

    return bestGPU;
  }

  async submitPrompt(prompt) {
    const gpuId = await this.selectBestGPU();
    console.log(`Selected GPU ${gpuId}`);

    return this.clients[gpuId].submitPrompt(prompt);
  }
}

// 使用
const lbClient = new LoadBalancedComfyUI();

// 自动选择最空闲的 GPU
const result = await lbClient.submitPrompt(workflow);
console.log(`Task assigned to GPU ${result.gpu_id}`);
```

### 场景 3: 并行提交

```javascript
async function parallelGeneration() {
  const prompts = [
    { /* workflow 1 */ },
    { /* workflow 2 */ },
    { /* workflow 3 */ },
    { /* workflow 4 */ }
  ];

  // 并行提交到 4 个 GPU
  const results = await Promise.all(
    prompts.map((prompt, idx) => {
      const port = 8181 + idx;
      return fetch(`http://localhost:${port}/prompt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt })
      }).then(r => r.json());
    })
  );

  console.log('All tasks submitted:', results);

  // 等待所有任务完成
  // ... 通过 WebSocket 或轮询监听
}

parallelGeneration();
```

---

## 📊 监控面板示例

```html
<!DOCTYPE html>
<html>
<head>
  <title>ComfyUI Multi-GPU Monitor</title>
  <style>
    .gpu-status {
      border: 1px solid #ccc;
      padding: 10px;
      margin: 10px;
      border-radius: 5px;
    }
    .gpu-idle { background: #dff0d8; }
    .gpu-busy { background: #fcf8e3; }
    .gpu-full { background: #f2dede; }
  </style>
</head>
<body>
  <h1>ComfyUI Multi-GPU Status</h1>
  <div id="status"></div>

  <script>
    async function updateStatus() {
      const response = await fetch('http://localhost:8188/queue/all');
      const data = await response.json();

      const statusDiv = document.getElementById('status');
      statusDiv.innerHTML = '';

      data.queues.forEach(q => {
        const div = document.createElement('div');
        div.className = 'gpu-status';

        // 根据负载设置颜色
        if (q.running_count === 0 && q.pending_count === 0) {
          div.classList.add('gpu-idle');
        } else if (q.pending_count > 5) {
          div.classList.add('gpu-full');
        } else {
          div.classList.add('gpu-busy');
        }

        div.innerHTML = `
          <h3>GPU ${q.gpu_id}</h3>
          <p>Running: ${q.running_count}</p>
          <p>Pending: ${q.pending_count}</p>
          <p>Port: ${8181 + q.gpu_id}</p>
        `;

        statusDiv.appendChild(div);
      });

      // 显示总计
      const totalDiv = document.createElement('div');
      totalDiv.innerHTML = `
        <h3>Total</h3>
        <p>Running: ${data.total_running}</p>
        <p>Pending: ${data.total_pending}</p>
      `;
      statusDiv.appendChild(totalDiv);
    }

    // 每 2 秒更新一次
    setInterval(updateStatus, 2000);
    updateStatus();
  </script>
</body>
</html>
```

---

## 🧪 测试脚本

我创建了一个测试脚本 `test_multi_gpu_endpoints.sh`，可以验证所有端点：

```bash
./test_multi_gpu_endpoints.sh
```

**输出示例**：
```
🧪 Testing Multi-GPU Endpoints
================================

Checking if ComfyUI is running...
✅ ComfyUI is running

Checking if Nginx is configured...
✅ All ports accessible

================================
Testing All Endpoints
================================

--- GPU 0 (Port 8181) ---
Testing GPU 0 (port 8181) - GET /queue
  ✅ Success (HTTP 200)

Testing GPU 0 (port 8181) - POST /upload/image
  ✅ Endpoint accessible (HTTP 400)

...
```

---

## 🎯 最佳实践

### 1. **单用户场景** - 用户选择 GPU

```javascript
// 让用户选择 GPU
const selectedGPU = document.getElementById('gpu-select').value;
const port = 8181 + parseInt(selectedGPU);

// 所有操作都使用该端口
const baseUrl = `http://localhost:${port}`;
```

### 2. **多用户场景** - 自动负载均衡

```javascript
// 每个请求前查询负载
const gpuId = await selectLeastLoadedGPU();
const port = 8181 + gpuId;
```

### 3. **批处理场景** - 并行提交

```javascript
// 批量任务平均分配到 4 个 GPU
const tasks = [...]; // 100 个任务
const chunkSize = Math.ceil(tasks.length / 4);

for (let gpuId = 0; gpuId < 4; gpuId++) {
  const chunk = tasks.slice(gpuId * chunkSize, (gpuId + 1) * chunkSize);
  const port = 8181 + gpuId;

  chunk.forEach(task => submitTask(port, task));
}
```

### 4. **调试场景** - 指定 GPU 测试

```bash
# 只在 GPU 2 上测试
PORT=8183

curl -X POST http://localhost:$PORT/upload/image -F "image=@test.png"
curl -X POST http://localhost:$PORT/prompt -d '...'
curl http://localhost:$PORT/queue
```

---

## ⚠️ 注意事项

1. **文件共享**：
   - 所有端口共享 `input/` 和 `output/` 文件夹
   - 上传的文件对所有 GPU 可见
   - 生成的文件对所有端口可见

2. **WebSocket clientId**：
   - 每个客户端需要唯一的 `clientId`
   - 不同端口的连接使用不同的 `clientId`

3. **队列独立**：
   - 每个 GPU 有独立的队列
   - 清空队列只影响对应的 GPU
   - 中断任务会自动查找所有队列

4. **限流**：
   - 每个端口有独立的限流（10 req/s，burst 20）
   - 不同端口的请求不互相影响

---

## 🎉 总结

**核心理念**：
> 将每个端口（8181-8184）**完全当作一个独立的 ComfyUI 服务器**使用

**所有接口都支持**：
- ✅ 图片上传/下载
- ✅ 任务提交
- ✅ 队列管理
- ✅ WebSocket 连接
- ✅ 历史查询

**唯一区别**：
- ⚡ 请求被路由到不同的 GPU
- ⚡ 队列状态反映对应 GPU 的状态

**使用建议**：
- 开发时：直接使用不同端口测试
- 生产时：通过负载均衡器智能分配
- 监控时：使用 `/queue/all` 获取全局状态
