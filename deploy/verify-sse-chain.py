"""Check a small live SSE chunk through the real gateway plus a buffering proxy.

Uses isolated, disposable Docker containers; no models or production data.
The same probe fails against the old gateway (headers and body arrive late).
"""
import argparse
import json
import subprocess
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

ORIGIN = r'''
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        if self.path == '/health':
            self.end_headers(); self.wfile.write(b'ok'); return
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()
        time.sleep(0.2)
        self.wfile.write(b': small-heartbeat\n\n'); self.wfile.flush()
        time.sleep(8)
    def log_message(self, *args): pass
ThreadingHTTPServer(('0.0.0.0', 9000), Handler).serve_forever()
'''
OUTER = '''
events {}
http {
    access_log off;
    server {
        listen 80;
        location / {
            proxy_pass http://gateway;
            proxy_buffering on;
        }
    }
}
'''


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, timeout=30).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app-image', default='gagent-app:latest')
    parser.add_argument('--gateway-image', default='gagent-gateway:latest')
    args = parser.parse_args()
    prefix = 'gagent-sse-check-' + uuid.uuid4().hex[:10]
    network = prefix + '-net'
    containers = []
    try:
        # Docker 20.10 omits published host ports on internal networks. These
        # fixtures perform no outbound requests; only the outer proxy is bound
        # to loopback, on an ephemeral port.
        docker('network', 'create', network)
        with tempfile.TemporaryDirectory(prefix=prefix) as directory:
            config = Path(directory) / 'outer.conf'
            config.write_text(OUTER)
            def start(suffix, *options):
                name = prefix + '-' + suffix
                containers.append(name)
                return docker('run', '-d', '--rm', '--name', name,
                              '--network', network, *options)
            start('origin', '--network-alias', 'app', '--entrypoint', 'python',
                  args.app_image, '-u', '-c', ORIGIN)
            start('gateway', '--network-alias', 'gateway', args.gateway_image)
            start('outer', '-p', '127.0.0.1::80', '-v', str(config) + ':/tmp/outer.conf:ro',
                  '--entrypoint', 'nginx', args.gateway_image,
                  '-c', '/tmp/outer.conf', '-g', 'daemon off;')
            port = json.loads(docker('inspect', prefix + '-outer'))[0]['NetworkSettings']['Ports']['80/tcp'][0]['HostPort']
            base = 'http://127.0.0.1:' + port
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 20
            while True:
                try:
                    with opener.open(base + '/health', timeout=1) as response:
                        if response.read() == b'ok': break
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError('SSE fixture readiness failed')
                time.sleep(0.2)
            started = time.monotonic()
            with opener.open(base + '/chat/runs/probe/events', timeout=3) as response:
                chunk = response.read(len(b': small-heartbeat\n\n'))
            elapsed = time.monotonic() - started
            assert chunk == b': small-heartbeat\n\n', chunk
            assert elapsed < 3, f'Buffered SSE: {elapsed:.3f}s'
            print(json.dumps({'small_chunk_seconds': round(elapsed, 3), 'passed': True}))
    finally:
        for name in reversed(containers):
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=30)
        subprocess.run(['docker', 'network', 'rm', network], capture_output=True, timeout=30)


if __name__ == '__main__':
    main()
