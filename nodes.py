import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import numpy as np
from PIL import Image
import torch
import folder_paths
import comfy.model_management as mm
from comfy_api.latest import InputImpl

from .external_api import ExternalAPI
from .llm import LocalGGUF
from .scene import prompts, write_scene, extract_code

ROOT = Path(__file__).resolve().parent
MODEL_DIR = Path(folder_paths.models_dir) / 'llm' / 'Qwen3.8'


def model_names(projector=False):
    return sorted(p.name for p in MODEL_DIR.glob('*.gguf') if ('mmproj' in p.name.lower()) == projector)


def resolve_model(name, projector=False):
    path = (MODEL_DIR / name).resolve()
    if path.parent != MODEL_DIR.resolve() or name not in model_names(projector):
        raise FileNotFoundError(f'Model not found in {MODEL_DIR}: {name}')
    return path


def image_tensor(path):
    with Image.open(path) as image:
        array = np.array(image.convert('RGB'), dtype=np.float32) / 255.0
    return torch.from_numpy(array).unsqueeze(0)


def render_scene(target, runtime, timeout_seconds, mode='video'):
    python = runtime.get('python') or sys.executable
    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    if runtime.get('browser_executable'):
        env['CVM_BROWSER_EXECUTABLE'] = runtime['browser_executable']
    log_path = target / 'render.log'
    with log_path.open('w', encoding='utf-8') as log:
        proc = subprocess.Popen([python, str(ROOT / 'runtime/render.py'), mode, '--scene', str(target)],
                                stdout=log, stderr=subprocess.STDOUT, env=env,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        started = time.monotonic()
        try:
            while proc.poll() is None:
                mm.throw_exception_if_processing_interrupted()
                if time.monotonic() - started > timeout_seconds:
                    raise TimeoutError(f'Three.js render timed out. See {log_path}')
                time.sleep(0.2)
        finally:
            if proc.poll() is None:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    proc.terminate()
                proc.wait(timeout=10)
    if proc.returncode:
        tail = log_path.read_text(encoding='utf-8')[-3500:]
        raise RuntimeError(f'Three.js render failed. Scene saved at {target}\n{tail}')


class CVMLocalThreeJS:
    @classmethod
    def INPUT_TYPES(cls):
        models = model_names() or ["None"]
        default = 'Qwen3.8-27B-UD-Q4_K_M.gguf'
        return {'required': {
            'prompt': ('STRING', {'multiline': True, 'default': '一辆红色小车沿公路从左向右行驶，路边有树木和房屋。低多边形风格，镜头平滑跟随。'}),
            'llm_model': (models, {'default': default if default in models else (models[0] if models else '')}),
            'vision_model': (['None'] + model_names(True), {'default': 'None', 'tooltip': '选择 mmproj 后，会用分镜预览进行一次视觉检查与代码修订；None 仅生成场景代码。'}),
            'width': ('INT', {'default': 960, 'min': 64, 'max': 4096, 'step': 2}),
            'height': ('INT', {'default': 544, 'min': 64, 'max': 4096, 'step': 2}),
            'fps': ('INT', {'default': 24, 'min': 1, 'max': 120}),
            'duration': ('FLOAT', {'default': 5.0, 'min': 0.1, 'max': 120.0, 'step': 0.1}),
            'seed': ('INT', {'default': 42, 'min': 0, 'max': 0xffffffffffffffff}),
            'context_size': ('INT', {'default': 16384, 'min': 4096, 'max': 65536, 'step': 1024}),
            'max_tokens': ('INT', {'default': 6000, 'min': 512, 'max': 32768, 'step': 256, 'tooltip': '仅 local 模式生效；api 模式不发送此参数，由服务商决定输出上限。'}),
            'temperature': ('FLOAT', {'default': 0.3, 'min': 0.0, 'max': 2.0, 'step': 0.05}),
            'gpu_layers': (['auto', 'all', '0'], {'default': 'auto'}),
            'repair_attempts': ('INT', {'default': 1, 'min': 0, 'max': 3}),
            'render_timeout': ('INT', {'default': 600, 'min': 30, 'max': 7200}),
            'backend': (['local', 'api'], {'default': 'local', 'tooltip': 'local：本地 GGUF；api：外部 Chat Completions 兼容接口。'}),
            'api_base_url': ('STRING', {'default': '', 'tooltip': '填写服务商给出的 API 基础地址（包含 /v1 等前缀），也支持完整 /chat/completions 地址。'}),
            'api_model': ('STRING', {'default': '', 'tooltip': '服务商的模型 ID。API 模式不使用 llm_model。'}),
            'api_key': ('STRING', {'default': 'env:CODEVIDEO_API_KEY', 'tooltip': '支持直接密钥或 env:变量名；直接填写会随工作流/历史保存，分享前清空。无鉴权接口可留空。'}),
            'api_vision_review': ('BOOLEAN', {'default': False, 'tooltip': '将分镜预览发送给 API 做视觉检查，需要 api_model 支持图片。无需 mmproj。'}),
            'api_timeout': ('INT', {'default': 180, 'min': 10, 'max': 1800, 'tooltip': '每次连接/读取等待超时秒数；非流式请求等待结束或超时后才能响应停止。'}),
            'api_stream': ('BOOLEAN', {'default': True, 'tooltip': '流式输出。服务商不支持时关闭。'}),
            'api_options': ('STRING', {'default': '{}', 'multiline': True, 'tooltip': '额外 JSON 请求参数；null 删除参数。例如 {"temperature":null}。不要在这里填密钥。'}),
        }}

    RETURN_TYPES = ('VIDEO', 'IMAGE', 'IMAGE', 'STRING', 'STRING')
    RETURN_NAMES = ('video', 'first_frame', 'contact_sheet', 'scene_directory', 'scene_code')
    FUNCTION = 'generate'
    CATEGORY = 'CodeVideo/ThreeJS'
    OUTPUT_NODE = True

    def generate(self, prompt, llm_model, vision_model, width, height, fps, duration, seed,
                 context_size, max_tokens, temperature, gpu_layers, repair_attempts, render_timeout,
                 backend="local", api_base_url="", api_model="", api_key="env:CODEVIDEO_API_KEY",
                 api_vision_review=False, api_timeout=180, api_stream=True, api_options="{}"):
        if not prompt.strip():
            raise ValueError('请输入三维场景描述。')
        runtime_path = ROOT / 'runtime_config.json'
        runtime = json.loads(runtime_path.read_text(encoding='utf-8')) if runtime_path.is_file() else {}
        frames = max(1, round(duration * fps))
        system, user, spec = prompts(prompt, width, height, fps, frames)
        if backend == 'local' and max_tokens + 2048 > context_size:
            raise ValueError('context_size 至少应比 max_tokens 大 2048，留出提示词空间。')
        output_root = Path(folder_paths.get_output_directory()).resolve()
        job = output_root / 'codevideo_threejs' / (time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
        job.mkdir(parents=True)
        mm.throw_exception_if_processing_interrupted()
        if backend == 'local':
            model_path = resolve_model(llm_model)
            projector = resolve_model(vision_model, True) if vision_model != 'None' else None
            configured_executable = runtime.get('llama_executable')
            if not configured_executable:
                raise FileNotFoundError('本地模式需要 runtime_config.json 中的 llama_executable；请参考 runtime_config.example.json。')
            executable = Path(configured_executable)
            if not executable.is_absolute():
                executable = ROOT / executable
            if not executable.is_file():
                raise FileNotFoundError('缺少本地推理运行库，请检查 runtime_config.json 中的 llama_executable。')
            mm.unload_all_models()
            mm.soft_empty_cache()
            client = LocalGGUF(executable, model_path, context_size, gpu_layers, job / 'llm.log',
                               mm.throw_exception_if_processing_interrupted, vision_model=projector)
            review_enabled = projector is not None
        elif backend == 'api':
            client = ExternalAPI(api_base_url, api_model, api_key, api_timeout, api_stream,
                                 api_options, mm.throw_exception_if_processing_interrupted)
            review_enabled = api_vision_review
        else:
            raise ValueError('backend 必须是 local 或 api。')
        report = dict(backend=backend, model=llm_model if backend == 'local' else api_model,
                      vision_model=vision_model if backend == 'local' else None,
                      visual_review_enabled=review_enabled, seed=seed,
                      settings=spec['settings'], attempts=[])
        with client as llm:
            response = llm.complete(system, user, max_tokens, temperature, seed)
            for attempt in range(repair_attempts + 1):
                target = job / f'attempt_{attempt}'
                try:
                    write_scene(target, response, spec)
                    render_scene(target, runtime, render_timeout, 'preview')
                    report['attempts'].append(dict(attempt=attempt, valid=True))
                    break
                except (ValueError, RuntimeError) as error:
                    report['attempts'].append(dict(attempt=attempt, error=str(error)))
                    (job / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                    if attempt == repair_attempts:
                        raise
                    repair = (user + '\n\nThe previous code failed browser validation. Fix it and return the complete module.\n'
                              + str(error)[-3500:] + '\nPrevious code:\n' + response)
                    response = llm.complete(system, repair, max_tokens, temperature, seed)
            if review_enabled:
                review = (user + '\n\nInspect the attached rendered contact sheet. Correct camera framing, missing objects, '
                          'occlusion or motion errors. Return the complete improved module, or the unchanged module if correct.\n'
                          'Current source:\n' + extract_code(response))
                revised = llm.complete(system, review, max_tokens, temperature, seed, image_path=target / 'preview.png')
                review_target = job / 'visual_review'
                try:
                    write_scene(review_target, revised, spec)
                    render_scene(review_target, runtime, render_timeout, 'preview')
                    target, response = review_target, revised
                    report['visual_review'] = 'applied'
                except (ValueError, RuntimeError) as error:
                    report['visual_review'] = 'rejected_invalid_revision'
                    report['visual_review_error'] = str(error)
                    print(f'[CodeVideo] 视觉修订未通过渲染检查，保留初稿。日志：{job}')
        # GPU memory is released before encoding the full video.
        render_scene(target, runtime, render_timeout)
        video_path = target / 'case/reconstruction.mp4'
        report['scene_directory'] = str(target)
        report['video'] = str(video_path)
        (job / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        subfolder = target.relative_to(output_root).as_posix()
        return {'ui': {'images': [{'filename': 'preview.png', 'subfolder': subfolder, 'type': 'output'}]},
                'result': (InputImpl.VideoFromFile(str(video_path)), image_tensor(target / 'first_frame.png'),
                           image_tensor(target / 'preview.png'), str(target), extract_code(response))}


NODE_CLASS_MAPPINGS = {'CVMLocalThreeJS': CVMLocalThreeJS}
NODE_DISPLAY_NAME_MAPPINGS = {'CVMLocalThreeJS': 'CodeVideo LLM 三维场景（本地/API）'}




