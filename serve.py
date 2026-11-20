"""Sirve la app (como `python -m http.server`) pero sin que el navegador guarde
versiones viejas: cada vez que abrís la app, el navegador pregunta si cambió algo.

Uso:  python serve.py          (puerto 5500)
      python serve.py 4000
"""
import http.server
import socketserver
import sys


class NoCache(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


port = int(sys.argv[1]) if len(sys.argv) > 1 else 5500
socketserver.ThreadingTCPServer.allow_reuse_address = True
with socketserver.ThreadingTCPServer(("0.0.0.0", port), NoCache) as httpd:
    print(f"App en http://localhost:{port}")
    httpd.serve_forever()
