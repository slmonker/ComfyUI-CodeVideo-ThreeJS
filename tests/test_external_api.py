import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from external_api import ExternalAPI, completion_url

CODE = '''export function createScene({THREE,scene,camera}) {
const mesh=new THREE.Mesh(new THREE.BoxGeometry(),new THREE.MeshNormalMaterial());
scene.add(mesh);camera.position.set(3,2,4);camera.lookAt(0,0,0);
return function update(t){mesh.rotation.y=t;};
}'''


class Handler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append((self.path, dict(self.headers), payload))
        model = payload['model']
        if model == 'unauthorized':
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'echo secret-value')
            return
        if model == 'redirect':
            self.send_response(302)
            self.send_header('Location', '/other-endpoint')
            self.end_headers()
            return
        code = CODE
        if model == 'repair' and 'Previous code:' not in str(payload['messages']):
            code = 'export function createScene(){throw new Error("test repair");}'
        reason = 'length' if model == 'truncated' else 'stop'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream' if payload['stream'] else 'application/json')
        self.end_headers()
        if payload['stream']:
            for part in (code[:40], code[40:]):
                self.wfile.write(('data: '+json.dumps({'choices':[{'delta':{'content':part},'finish_reason':None}]})+'\n\n').encode())
            if model != 'broken-stream':
                self.wfile.write(('data: '+json.dumps({'choices':[{'delta':{},'finish_reason':reason}]})+'\n\ndata: [DONE]\n\n').encode())
        else:
            self.wfile.write(json.dumps({'choices':[{'message':{'content':code},'finish_reason':reason}]}).encode())


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/v1'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def client(self, model='test', stream=True, options='{}', key='secret-value'):
        return ExternalAPI(self.url, model, key, 5, stream, options, lambda: None)

    def test_paths(self):
        self.assertEqual(completion_url('https://example.test/compatible/v1/'), 'https://example.test/compatible/v1/chat/completions')
        self.assertEqual(completion_url('https://example.test/v1/chat/completions'), 'https://example.test/v1/chat/completions')
        for url in ('file:///secret', 'https://user:secret@example.test', 'https://example.test?api_key=secret'):
            with self.assertRaises(ValueError):
                completion_url(url)

    def test_stream_and_json(self):
        for stream in (True, False):
            self.assertEqual(self.client(stream=stream).complete('system','场景',600,0.3,42), CODE)
            path, headers, body = Handler.requests[-1]
            self.assertEqual(path, '/v1/chat/completions')
            self.assertEqual(headers['Authorization'], 'Bearer secret-value')
            self.assertNotIn('seed', body)
            self.assertNotIn('max_tokens', body)
            self.assertNotIn('max_completion_tokens', body)
            self.assertNotIn('chat_template_kwargs', body)

    def test_options_and_environment_key(self):
        with patch.dict(os.environ, {'CVM_TEST_KEY':'secret-value'}):
            self.client(options='{"temperature":null,"max_tokens":null,"max_completion_tokens":800}',key='env:CVM_TEST_KEY').complete('s','u',600,0.3,42)
        body = Handler.requests[-1][2]
        self.assertNotIn('temperature', body)
        self.assertNotIn('max_tokens', body)
        self.assertEqual(body['max_completion_tokens'],800)
        for options in ('[]', 'invalid', '{"messages":[]}'):
            with self.assertRaises(ValueError):
                self.client(options=options)
        with self.assertRaises(ValueError):
            self.client(key='env:CVM_MISSING_TEST_KEY')

    def test_errors_and_no_secret_echo(self):
        for model in ('unauthorized', 'redirect', 'broken-stream'):
            with self.assertRaises(RuntimeError) as error:
                self.client(model=model).complete('s','u',600,0.3,42)
            self.assertNotIn('secret-value', str(error.exception))
        self.assertEqual(self.client(model='truncated').complete('s','u',600,0.3,42), CODE)

    def test_node_api_without_local_models_including_repair_and_vision(self):
        if not os.environ.get('COMFYUI_TEST_ROOT'):
            self.skipTest('Set COMFYUI_TEST_ROOT for the render integration test')
        comfy=Path(os.environ['COMFYUI_TEST_ROOT'])
        sys.path.insert(0,str(comfy))
        spec=importlib.util.spec_from_file_location('cvm_api_test',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
        package=importlib.util.module_from_spec(spec)
        sys.modules[spec.name]=package
        spec.loader.exec_module(package)
        nodes=sys.modules['cvm_api_test.nodes']
        with tempfile.TemporaryDirectory(prefix='cvm_api_') as out:
            with patch.object(nodes.folder_paths,'get_output_directory',return_value=out), \
                 patch.object(nodes,'resolve_model',side_effect=AssertionError('API must not resolve local models')), \
                 patch.object(nodes,'LocalGGUF',side_effect=AssertionError('API must not start local runtime')), \
                 patch.object(nodes.mm,'unload_all_models',side_effect=AssertionError('API must not unload GPU models')):
                result=nodes.CVMLocalThreeJS().generate(
                    prompt='旋转方块',llm_model='not-installed.gguf',vision_model='not-installed.gguf',
                    width=320,height=180,fps=2,duration=1,seed=42,context_size=4096,max_tokens=6000,
                    temperature=0.3,gpu_layers='auto',repair_attempts=1,render_timeout=60,
                    backend='api',api_base_url=self.url,api_model='repair',api_key='secret-value',
                    api_vision_review=True,api_timeout=5,api_stream=True,api_options='{}')
            video,first,sheet,directory,code=result['result']
            self.assertTrue(Path(video.get_stream_source()).is_file())
            self.assertEqual(list(first.shape),[1,180,320,3])
            report=json.loads((Path(directory).parent/'report.json').read_text(encoding='utf-8'))
            self.assertEqual(len(report['attempts']),2)
            self.assertEqual(report['visual_review'],'applied')
            self.assertEqual(report['backend'],'api')
            self.assertNotIn('secret-value', json.dumps(report))
            visual=[r[2] for r in Handler.requests if isinstance(r[2]['messages'][1]['content'],list)]
            self.assertTrue(visual[-1]['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,'))


if __name__=='__main__':
    unittest.main(verbosity=2)
