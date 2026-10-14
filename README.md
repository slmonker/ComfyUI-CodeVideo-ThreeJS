# ComfyUI CodeVideo Three.js

一个 ComfyUI 自定义节点：使用本地 GGUF 或 OpenAI-compatible 外部 API 生成可编辑的程序化 Three.js 场景，并渲染为视频。

## 功能

- 单节点完成 LLM 调用、Three.js 场景生成、浏览器验证和视频渲染。
- `backend=local`：从 ComfyUI 的 `models/llm/Qwen3.8` 扫描 GGUF；可选 `mmproj` 做视觉检查。
- `backend=api`：调用 OpenAI-compatible Chat Completions 接口，不需要本地 GGUF。
- API 模式默认不发送 `max_tokens`，由服务商决定输出上限；如果服务商要求指定上限，可在 `api_options` 中填写服务商参数。
- 代码不完整或浏览器验证失败时，可使用 `repair_attempts` 自动修复。
- 输出视频、首帧、五帧 contact sheet、可编辑场景目录和场景 JavaScript。

## 安装

将本仓库目录放入：

```text
ComfyUI/custom_nodes/ComfyUI-CodeVideo-ThreeJS
```

渲染器需要 Python 环境中的 `playwright` 和 `Pillow`，并需要本机可用的 Chromium/Edge 浏览器以及 `ffmpeg` / `ffprobe`。可以安装：

```bash
python -m pip install -r requirements-render.txt
python -m playwright install chromium
```

也可以在 `runtime_config.json` 中指定已有的 Python、浏览器和本地 llama.cpp 运行库。配置模板见 `runtime_config.example.json`。该文件不包含任何模型或密钥。

## 本地 GGUF 模式

节点从当前 ComfyUI 的模型目录扫描：

```text
ComfyUI/models/llm/Qwen3.8/*.gguf
```

设置：

```text
backend: local
llm_model: 选择 GGUF
vision_model: None 或选择 mmproj
```

本地模式使用节点内部的 llama.cpp 进程，不需要连接其他 Llama-cpp 节点，也不需要 Ollama。`context_size`、`gpu_layers` 和 `max_tokens` 只对本地模式有实际作用。

如果使用仓库外的 llama.cpp，可在 `runtime_config.json` 指定：

```json
{
  "python": "",
  "browser_executable": "",
  "llama_executable": "runtime/llama/llama-server.exe"
}
```

## API 模式

设置：

```text
backend: api
api_base_url: https://provider.example/v1
api_model: provider-model-id
api_key: env:CODEVIDEO_API_KEY
```

`api_key` 推荐使用环境变量形式。Windows PowerShell 示例：

```powershell
$env:CODEVIDEO_API_KEY = "your-key"
```

如果 API 不需要鉴权，可以留空。API 地址既可以填基础地址，例如 `/v1`，也可以填完整的 `/chat/completions` 地址。

默认请求不会包含 `max_tokens`。如果服务商要求使用特定参数，可以填写 `api_options`，例如：

```json
{"max_completion_tokens":12000}
```

视觉检查需要 API 模型支持图片输入，并打开：

```text
api_vision_review: true
```

不要把真实 API Key 写入工作流后分享；工作流示例使用 `env:CODEVIDEO_API_KEY`。

## 工作流

- `workflows/本地Qwen_三维场景_单节点.json`：本地 GGUF 示例。
- `workflows/api_prompt.json`：外部 API 示例，使用占位地址和环境变量密钥。

## 输出

节点输出：

- `video`：ComfyUI VIDEO，可连接内置 Save Video。
- `first_frame`：视频首帧 IMAGE。
- `contact_sheet`：从视频抽取的五帧横向预览 IMAGE。
- `scene_directory`：包含 `case/scene.js`、预览、镜头轨迹和 MP4 的可编辑场景目录。
- `scene_code`：生成的 Three.js JavaScript。

任务目录默认位于：

```text
ComfyUI/output/codevideo_threejs/
```

## 说明

这是 CodeVideoModel 的 Three.js 代理场景阶段的 ComfyUI 适配，不包含后续 MiniMax-H3 视频生成。生成结果偏向程序化、低多边形和结构参考，最终视觉质量取决于所选模型与提示词。

渲染器、预览器和 Three.js 资源来自 `code-video-model/CodeVideoModel` 的固定提交 `404e03d67c2977b441622ce94411470bb84984a8`。Three.js 为 r160，许可证见 `vendor/LICENSE.threejs`；第三方说明见 `THIRD_PARTY_NOTICES.md`。
