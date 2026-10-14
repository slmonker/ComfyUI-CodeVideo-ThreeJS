# ComfyUI CodeVideo Three.js

A single ComfyUI custom node that uses either a local GGUF model or an OpenAI-compatible API to generate editable procedural Three.js scenes and render them to video.

[简体中文](README.zh-CN.md)

## Highlights

- One node for LLM prompting, Three.js scene generation, browser validation, and video rendering.
- Local GGUF mode scans `ComfyUI/models/llm/Qwen3.8` and can optionally use an `mmproj` projector for visual review.
- API mode supports OpenAI-compatible Chat Completions endpoints and does not require local GGUF files.
- API requests do not automatically include `max_tokens`; the provider controls the output limit unless a provider-specific option is supplied through `api_options`.
- Browser validation failures and incomplete code can be sent through configurable repair attempts.
- Outputs include a video, first frame, contact sheet, editable scene directory, and generated JavaScript.

## Installation

Copy this repository into:

```text
ComfyUI/custom_nodes/ComfyUI-CodeVideo-ThreeJS
```

The renderer requires Python packages listed in `requirements-render.txt`, a local Chromium/Edge browser, and `ffmpeg` / `ffprobe`:

```bash
python -m pip install -r requirements-render.txt
python -m playwright install chromium
```

If you already have a separate renderer environment, set its paths in a local, untracked `runtime_config.json`. Use `runtime_config.example.json` as a template. The example contains no model files or credentials.

Restart ComfyUI after installing or updating the node.

## Local GGUF mode

The node scans:

```text
ComfyUI/models/llm/Qwen3.8/*.gguf
```

Set:

```text
backend: local
llm_model: your GGUF model
vision_model: None or an mmproj file
```

The node starts and stops its own llama.cpp process. You do not need to connect a separate Llama-cpp loader node and you do not need Ollama. `context_size`, `gpu_layers`, and `max_tokens` are used by local mode.

A local `runtime_config.json` can point to an external llama.cpp runtime:

```json
{
  "python": "",
  "browser_executable": "",
  "llama_executable": "runtime/llama/llama-server.exe"
}
```

The repository intentionally does not include CUDA DLLs, llama.cpp binaries, or model weights.

## API mode

Set:

```text
backend: api
api_base_url: https://provider.example/v1
api_model: provider-model-id
api_key: env:CODEVIDEO_API_KEY
```

Using an environment variable for the key is recommended:

```powershell
$env:CODEVIDEO_API_KEY = "your-key"
```

Leave `api_key` empty for an endpoint that does not require authentication. The base URL may end at `/v1`, or it may be a complete `/chat/completions` URL.

By default, the node omits `max_tokens` in API mode. If a provider requires a different output-limit parameter, use `api_options`, for example:

```json
{"max_completion_tokens":12000}
```

Enable `api_vision_review` only when the selected API model accepts image inputs. Do not put a real API key into a workflow that will be shared; the included examples use `env:CODEVIDEO_API_KEY`.

## Workflows

- `workflows/本地Qwen_三维场景_单节点.json` — local GGUF example.
- `workflows/API_三维场景_单节点.json` — API example with placeholder endpoint/model values.
- `workflows/api_prompt.json` — API-format prompt graph with placeholder values.

## Outputs

- `video` — ComfyUI `VIDEO`, compatible with the built-in Save Video node.
- `first_frame` — the first rendered frame as `IMAGE`.
- `contact_sheet` — five sampled frames arranged horizontally as `IMAGE`.
- `scene_directory` — an editable directory containing `case/scene.js`, preview images, camera trajectory data, and the MP4.
- `scene_code` — the generated Three.js JavaScript source.

Jobs are written under:

```text
ComfyUI/output/codevideo_threejs/
```

## Scope

This project adapts the Three.js proxy-scene stage of CodeVideoModel for ComfyUI. It does not run the later MiniMax-H3 video generation stage. The output is intended as a procedural structural reference; visual quality depends on the selected model and prompt.

## Attribution

The renderer, viewer, and Three.js assets are adapted from `code-video-model/CodeVideoModel` at commit `404e03d67c2977b441622ce94411470bb84984a8`. See `LICENSE`, `vendor/LICENSE.threejs`, and `THIRD_PARTY_NOTICES.md`.
