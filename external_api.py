"""OpenAI-compatible Chat Completions client for the scene node."""
import base64
import io
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from PIL import Image


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def completion_url(base_url):
    parsed = urlsplit(base_url.strip())
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('api_base_url 必须是 http(s) 地址，例如 https://服务商地址/v1。')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('API 地址不能包含账号、密码、查询参数或片段；密钥请填在 api_key。')
    path = parsed.path.rstrip('/')
    if not path.endswith('/chat/completions'):
        path += '/chat/completions'
    return urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))


def resolve_key(value):
    value = value.strip()
    if value.startswith('env:'):
        name = value[4:].strip()
        key = os.environ.get(name, '').strip()
        if not key:
            raise ValueError(f'未设置环境变量 {name}。可在 api_key 直接填写密钥，或设置变量后重启 ComfyUI。')
        return key
    return value


def text_content(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ''.join(part.get('text', '') for part in value
                       if isinstance(part, dict) and isinstance(part.get('text'), str))
    return ''


class ExternalAPI:
    def __init__(self, base_url, model, api_key, timeout, stream, options, check_interrupt):
        self.url = completion_url(base_url)
        self.model = model.strip()
        if not self.model:
            raise ValueError('API 模式需要填写 api_model。')
        self.key = resolve_key(api_key)
        self.timeout = timeout
        self.stream = stream
        self.check_interrupt = check_interrupt
        self.http = build_opener(NoRedirects())
        try:
            self.options = json.loads(options)
        except json.JSONDecodeError as error:
            raise ValueError('api_options 必须是合法 JSON 对象，例如 {}。') from error
        if not isinstance(self.options, dict):
            raise ValueError('api_options 必须是 JSON 对象。')
        if {'model', 'messages', 'stream'} & self.options.keys():
            raise ValueError('api_options 不能覆盖 model、messages 或 stream，请使用对应节点参数。')

    def complete(self, system, user, max_tokens, temperature, seed, image_path=None):
        self.check_interrupt()
        content = user
        if image_path is not None:
            buffer = io.BytesIO()
            with Image.open(image_path) as image:
                image.convert('RGB').save(buffer, format='JPEG', quality=85)
            content = [{'type': 'text', 'text': user}, {'type': 'image_url', 'image_url': {
                'url': 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')}}]
        payload = {'model': self.model,
                   'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}],
                   'temperature': temperature, 'stream': self.stream}
        # Omit vendor-specific seed/reasoning parameters unless explicitly supplied.
        for key, value in self.options.items():
            if value is None:
                payload.pop(key, None)
            else:
                payload[key] = value
        headers = {'Content-Type': 'application/json', 'Accept': 'text/event-stream' if self.stream else 'application/json'}
        if self.key:
            headers['Authorization'] = 'Bearer ' + self.key
        request = Request(self.url, data=json.dumps(payload).encode('utf-8'), headers=headers)
        try:
            with self.http.open(request, timeout=self.timeout) as response:
                if 'text/event-stream' in response.headers.get('Content-Type', ''):
                    text, reason = self.read_stream(response)
                else:
                    self.check_interrupt()
                    item = json.loads(response.read())
                    self.check_interrupt()
                    if item.get('error'):
                        raise RuntimeError('API 返回错误，请检查模型名、额度及服务商状态。')
                    if not item.get('choices'):
                        raise ValueError('API 响应缺少 choices；需要 Chat Completions 兼容接口。')
                    choice = item['choices'][0]
                    text = text_content(choice.get('message', {}).get('content'))
                    reason = choice.get('finish_reason')
        except HTTPError as error:
            # Do not include provider response bodies: these can echo keys or prompts.
            hints = {401: '密钥无效或缺失', 403: '无模型访问权限', 404: '地址或模型不存在',
                     429: '请求限流或余额不足', 400: '参数不被模型接受，请检查 api_options 和视觉支持'}
            raise RuntimeError(f'API HTTP {error.code}：{hints.get(error.code, "服务商返回错误")}。') from None
        except (URLError, TimeoutError):
            raise RuntimeError('API 连接失败或等待超时，请检查地址、网络及 api_timeout。') from None
        except json.JSONDecodeError:
            raise ValueError('API 返回了无法解析的 JSON；请确认使用 Chat Completions 接口。') from None
        if reason in ('length', 'max_tokens'):
            if text.strip():
                return text
            raise ValueError('API 输出达到 token 上限且没有返回代码；请提高 max_tokens 或相应 api_options。')
        if reason in ('content_filter', 'tool_calls'):
            raise ValueError(f'API 未返回完整场景代码，finish_reason={reason}。')
        if not text.strip():
            raise ValueError('API 没有返回场景代码（message.content 为空）。')
        return text

    def read_stream(self, response):
        pieces, reason, completed = [], None, False
        data = []
        for line in response:
            self.check_interrupt()
            line = line.decode('utf-8').rstrip('\r\n')
            if line.startswith('data:'):
                data.append(line[5:].lstrip())
            elif not line and data:
                raw = '\n'.join(data)
                data.clear()
                if raw == '[DONE]':
                    completed = True
                    break
                item = json.loads(raw)
                if item.get('error'):
                    raise RuntimeError('API 流式响应报告错误，请检查服务商状态。')
                if item.get('choices'):
                    choice = item['choices'][0]
                    pieces.append(text_content(choice.get('delta', {}).get('content')))
                    reason = choice.get('finish_reason') or reason
        if not completed and reason is None:
            raise RuntimeError('API 流式连接提前结束，未收到完整响应。')
        return ''.join(pieces), reason

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


