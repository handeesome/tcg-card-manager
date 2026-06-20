#!/usr/bin/env python3
"""TCG 持仓看板 - 本地服务器"""
import http.server
import os
import json
import time
import shutil
from urllib.parse import urlparse

PORT = 8765
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

http.server.SimpleHTTPRequestHandler.extensions_map['.json'] = 'application/json'

import json
class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        if self.path.startswith('/web/') or self.path.endswith('.json'):
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
        super().end_headers()

    def do_GET(self):
        if self.path.startswith('http://'):
            self.path = urlparse(self.path).path
        super().do_GET()

    def do_POST(self):
        # 受限 API：仅处理 /api/save-portfolio
        if self.path == '/api/save-portfolio':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length) if length else b''
            try:
                data = json.loads(body.decode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'Invalid JSON')
                return
            # 写入前备份已在外部创建
            portfolio_path = os.path.join(ROOT, 'data', 'portfolio.json')
            tmp_path = portfolio_path + '.tmp'
            try:
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, portfolio_path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'OK')
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode('utf-8'))
            return
        # fallback to default
        if self.path.startswith('http://'):
            self.path = urlparse(self.path).path
        super().do_GET()

print(f"TCG Dashboard → http://localhost:{PORT}/web/")
print(f"API available: POST /api/save-portfolio (local only)")
print(f"按 Ctrl+C 停止")

http.server.HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
