"""Node-owned local GGUF inference process, with no external service setup."""
import base64
import io
import json
from PIL import Image
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler


class LocalGGUF:
    def __init__(self, executable, model, context_size, gpu_layers, log_path, check_interrupt, vision_model=None):
        self.log_path = Path(log_path)
        self.log = self.log_path.open('w', encoding='utf-8')
        self.process = None
        self.check_interrupt = check_interrupt
        self.http = build_opener(ProxyHandler({}))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.base_url = f'http://127.0.0.1:{port}'
        self.key = secrets.token_urlsafe(32)
        executable = Path(executable).resolve()
        env = os.environ.copy()
        env['PATH'] = str(executable.parent) + os.pathsep + env.get('PATH', '')
        args = [str(executable), '-m', str(model), '--host', '127.0.0.1', '--port', str(port),
                '--ctx-size', str(context_size), '--parallel', '1', '--gpu-layers', str(gpu_layers),
                '--flash-attn', 'auto', '--fit', 'on', '--fit-target', '2048',
                '--no-webui', '--api-key', self.key, '--jinja', '--reasoning', 'off',
                '--chat-template-kwargs', json.dumps({'enable_thinking': False})]
        if vision_model is not None:
            args += ["--mmproj", str(vision_model)]
        try:
            self.process = subprocess.Popen(args, stdout=self.log, stderr=subprocess.STDOUT, env=env,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                check_interrupt()
                if self.process.poll() is not None:
                    raise RuntimeError('GGUF load failed. ' + self.error_tail())
                try:
                    with self.http.open(Request(self.base_url + '/health', headers=self.headers()), timeout=1) as response:
                        if response.status == 200:
                            return
                except (URLError, TimeoutError):
                    pass
                time.sleep(0.25)
            raise TimeoutError('GGUF model load timed out. ' + self.error_tail())
        except BaseException:
            self.close()
            raise

    def headers(self):
        return {'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'}

    def error_tail(self):
        self.log.flush()
        return self.log_path.read_text(encoding='utf-8', errors='replace')[-3000:]

    def complete(self, system, user, max_tokens, temperature, seed, image_path=None):
        content = user
        if image_path is not None:
            buffer = io.BytesIO()
            with Image.open(image_path) as image:
                image.convert("RGB").save(buffer, format="JPEG", quality=85)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            content = [{"type": "text", "text": user},
                       {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encoded}}]
        payload = {'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}],
                   'max_tokens': max_tokens, 'temperature': temperature, 'top_p': 0.9,
                   'seed': int(seed) % 4294967295, 'stream': True,
                   'chat_template_kwargs': {'enable_thinking': False}}
        request = Request(self.base_url + '/v1/chat/completions', data=json.dumps(payload).encode(), headers=self.headers())
        pieces = []
        finish_reason = None
        try:
            with self.http.open(request, timeout=120) as response:
                for line in response:
                    self.check_interrupt()
                    if not line.startswith(b'data: '):
                        continue
                    raw = line[6:].strip()
                    if raw == b'[DONE]':
                        break
                    item = json.loads(raw)
                    if not item.get('choices'):
                        continue
                    choice = item['choices'][0]
                    pieces.append(choice.get('delta', {}).get('content') or '')
                    finish_reason = choice.get('finish_reason') or finish_reason
        except HTTPError as error:
            raise RuntimeError('Local GGUF generation failed: ' + error.read().decode(errors='replace')[:2000]) from error
        if finish_reason == 'length':
            raise ValueError('LLM reached max_tokens before completing the scene. Increase max_tokens/context_size.')
        content = ''.join(pieces)
        if not content.strip():
            raise ValueError('Local model returned no scene code. ' + self.error_tail())
        return content

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        self.log.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
