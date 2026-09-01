import requests, socket, threading
class _BlackHoleServer:
    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._stop = False
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
    def _accept_loop(self):
        self._sock.settimeout(0.05)
        conns = []
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
                conns.append(conn)
            except socket.timeout:
                continue
        for conn in conns:
            conn.close()
    def close(self):
        self._stop = True
        self._thread.join(timeout=1.0)
        self._sock.close()

server = _BlackHoleServer()
res = requests.get(f"http://127.0.0.1:{server.port}/", timeout=0.5)
print(res.status_code, res.content)
server.close()
