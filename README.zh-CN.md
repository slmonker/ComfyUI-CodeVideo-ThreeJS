# ComfyUI CodeVideo Three.js（中文说明）

这是一个 ComfyUI 自定义节点：可以调用本地 GGUF 模型或 OpenAI-compatible 外部 API，生成可编辑的程序化 Three.js 三维场景，并渲染为视频。

[English](README.md)

## 功能

- 单节点完成 LLM 调用、Three.js 场景生成、浏览器验证和视频渲染。
- `backend=local`：扫描 `ComfyUI/models/llm/Qwen3.8` 中的 GGUF 模型，可选 `mmproj` 做视觉检查。
- `backend=api`：调用 OpenAI-compatible Chat Completions 接口，不需要本地 GGUF。
- API 模式默认不发送 `max_tokens`，由服务商决定输出上限；如果服务商要求指定上限，可在 `api_options` 中填写服务商参数。
- 代码不完整或浏览器验证失败时，可以使用 `repair_attempts` 自动修复。
- 输出视频、首帧、五帧 contact sheet、可编辑场景目录和 JavaScript 代码。

## 安装

将仓库目录放入：

```text
ComfyUI/custom_nodes/ComfyUI-CodeVideo-ThreeJS
```

渲染器需要 `requirements-render.txt` 中的 Python 依赖、本机 Chromium/Edge 浏览器，以及 `ffmpeg` / `ffprobe`：

```bash
python -m pip install -r requirements-render.txt
python -m playwright install chromium
```

如果使用独立的渲染环境，可以在本地、未跟踪的 `runtime_config.json` 中指定路径。模板见 `runtime_config.example.json`。模板不包含模型文件或密钥。

安装或更新节点后请重启 ComfyUI。

## 本地 GGUF 模式

节点扫描以下目录：

```text
ComfyUI/models/llm/Qwen3.8/*.gguf
```

设置：

```text
backend: local
llm_model: 选择 GGUF 模型
vision_model: None 或选择 mmproj 文件
```

节点内部自行启动和关闭 llama.cpp 进程，不需要连接单独的 Llama-cpp 加载节点，也不需要 Ollama。`context_size`、`gpu_layers` 和 `max_tokens` 仅对本地模式生效。

本地 `runtime_config.json` 可以指定外部 llama.cpp 运行库：

```json
{
  "python": "",
  "browser_executable": "",
  "llama_executable": "runtime/llama/llama-server.exe"
}
```

仓库不会包含 CUDA DLL、llama.cpp 可执行文件或模型权重。

## API 模式

设置：

```text
backend: api
api_base_url: https://provider.example/v1
api_model: provider-model-id
api_key: env:CODEVIDEO_API_KEY
```

推荐使用环境变量保存密钥：

```powershell
$env:CODEVIDEO_API_KEY = "your-key"
```

如果接口不需要鉴权，可以将 `api_key` 留空。API 地址可以填写 `/v1` 基础地址，也可以填写完整的 `/chat/completions` 地址。

API 模式默认不发送 `max_tokens`。如果服务商要求使用其他输出长度参数，可以通过 `api_options` 填写，例如：

```json
{"max_completion_tokens":12000}
```

只有当 API 模型支持图片输入时，才打开 `api_vision_review`。不要把真实 API Key 写进需要分享的工作流；仓库示例使用 `env:CODEVIDEO_API_KEY`。

## 工作流

- `workflows/本地Qwen_三维场景_单节点.json`：本地 GGUF 示例。
- `workflows/API_三维场景_单节点.json`：外部 API 示例，使用占位地址和模型名。
- `workflows/api_prompt.json`：API 格式的提示词图，使用占位参数。

## 输出

- `video`：ComfyUI `VIDEO`，可连接内置 Save Video 节点。
- `first_frame`：视频首帧 `IMAGE`。
- `contact_sheet`：横向排列的五帧预览 `IMAGE`。
- `scene_directory`：包含 `case/scene.js`、预览图、镜头轨迹和 MP4 的可编辑场景目录。
- `scene_code`：生成的 Three.js JavaScript 源码。

任务默认保存到：

```text
ComfyUI/output/codevideo_threejs/
```

## 项目范围

本项目将 CodeVideoModel 的 Three.js 代理场景阶段适配到 ComfyUI，不包含后续 MiniMax-H3 视频生成阶段。输出主要用于程序化结构参考，最终质量取决于所选模型和提示词。

## 来源与许可证

渲染器、预览器和 Three.js 资源改编自 `code-video-model/CodeVideoModel` 的提交 `404e03d67c2977b441622ce94411470bb84984a8`。许可证请参见 `LICENSE`、`vendor/LICENSE.threejs` 和 `THIRD_PARTY_NOTICES.md`。
