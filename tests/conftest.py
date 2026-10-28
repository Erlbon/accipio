"""A small local web server for the engine and probe tests: no test ever touches the internet."""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

PAYLOAD = bytes(range(256)) * 4096          # 1 MiB with a recognisable pattern
SLOW_SIZE = 4 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code, headers, body=b""):
        self.send_response(code)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _route(self):
        path = self.path.split("?")[0]
        ranged = self.headers.get("Range") == "bytes=0-0"
        if path == "/files/payload.bin" or path == "/files/real.zip":
            mime = "application/zip" if path.endswith(".zip") else "application/octet-stream"
            if ranged:
                return self._send(206, {"Content-Type": mime, "Content-Range": f"bytes 0-0/{len(PAYLOAD)}"}, PAYLOAD[:1])
            return self._send(200, {"Content-Type": mime, "Accept-Ranges": "bytes"}, PAYLOAD)
        if path == "/cd":
            return self._send(200, {"Content-Type": "application/pdf",
                                    "Content-Disposition": 'attachment; filename="report final.pdf"'}, b"%PDF-1.7")
        if path == "/cd-star":
            return self._send(200, {"Content-Type": "application/epub+zip",
                                    "Content-Disposition": "attachment; filename=\"aoa.epub\"; filename*=UTF-8''%C3%A6%C3%B8%C3%A5.epub"}, b"PK")
        if path == "/cd-path":
            return self._send(200, {"Content-Disposition": 'attachment; filename="..\\..\\evil.exe"'}, b"MZ")
        if path == "/redirect":
            return self._send(302, {"Location": "/files/real.zip"})
        if path == "/get.php":
            return self._send(200, {"Content-Type": "application/zip"}, b"PK")
        if path == "/folder/":
            return self._send(200, {"Content-Type": "application/x-msdownload"}, b"MZ")
        if path == "/nohead":
            if self.command == "HEAD":
                return self._send(405, {})
            if ranged:
                return self._send(206, {"Content-Type": "video/mp4", "Content-Range": "bytes 0-0/1234"}, b"\0")
            return self._send(200, {"Content-Type": "video/mp4"}, b"\0" * 1234)
        if path == "/slow.bin":
            return self._slow(ranges=False)
        if path == "/slow-resumable.bin":
            return self._slow(ranges=True)
        if path == "/media/clip.mp4":                    # a plain video file: yt-dlp's generic extractor takes it
            return self._slow(ranges=True, mime="video/mp4", size=256 * 1024, pause=0.0)
        if path == "/media/slow.mp4":
            return self._slow(ranges=True, mime="video/mp4")
        return self._send(404, {"Content-Type": "text/plain"}, b"not here")

    def _slow(self, ranges, mime="application/octet-stream", size=SLOW_SIZE, pause=0.02):
        """``size`` bytes (4 MiB) at about 800 KiB/s; with ``ranges`` it can resume (``Range: bytes=N-``)."""
        start, end = 0, size - 1
        wanted = self.headers.get("Range", "")
        if ranges and wanted.startswith("bytes="):
            first, _, last = wanted[6:].partition("-")
            start, end = int(first), (int(last) if last else size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        if ranges:
            self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(end + 1 - start))
        self.end_headers()
        if self.command == "HEAD":
            return
        chunk = 16384
        try:
            for offset in range(start, end + 1, chunk):
                self.wfile.write(b"x" * min(chunk, end + 1 - offset))
                if pause:
                    time.sleep(pause)
        except OSError:
            pass                                 # the client went away (paused or removed)

    def handle(self):
        try:
            super().handle()
        except ConnectionError:
            pass                                 # aria2 hung up mid-request (pause, remove): not a test failure

    do_GET = _route
    do_HEAD = _route


@pytest.fixture
def web():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
