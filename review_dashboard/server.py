"""Review dashboard server (stdlib only).

Serves the static dashboard + page crops + each book's final zip, and
exposes the human-review API backed by qbank.review (append-only
ledgers, fingerprinted decisions, verified edits).

    python3 review_dashboard/server.py [--port 8000]
"""
import argparse
import json
import sys
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

from qbank import review  # noqa: E402


def books():
    for d in sorted(REPO.glob("qbank_output_*")):
        if (d / "split").is_dir():
            yield d.name.replace("qbank_output_", "").upper(), d


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(HERE), **kw)

    def do_GET(self):
        if self.path == "/api/queue":
            items = []
            for book, root in books():
                items += review.review_tables(root)
            return self._json(items)
        if self.path.startswith("/zip/"):
            book = self.path[len("/zip/"):].strip("/").upper()
            for b, root in books():
                if b == book:
                    z = root / "final_export.zip"
                    if z.exists():
                        self.send_response(200)
                        self.send_header("Content-Type", "application/zip")
                        self.send_header(
                            "Content-Disposition",
                            f'attachment; filename="{b}_final_export.zip"')
                        body = z.read_bytes()
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        return self.wfile.write(body)
            return self._json({"error": "no zip for book"}, 404)
        return super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)
        root = self._root(body.get("book"))
        if root is None:
            return self._json({"error": "unknown book"}, 404)
        if self.path == "/api/decision":
            row = review.record_decision(
                root, body["book"], body["q_id"], body["table_id"],
                body.get("action", "approve"), body.get("note", ""))
            return self._json(row)
        if self.path == "/api/edit":
            res = review.apply_table_edit(
                root, body["book"], body["q_id"], body["table_id"],
                body.get("markdown", ""))
            if res.get("ok") and body.get("action"):
                review.record_decision(
                    root, body["book"], body["q_id"], body["table_id"],
                    body["action"], "saved via edit")
            return self._json(res)
        return self._json({"error": "no route"}, 404)

    def _root(self, book):
        for b, root in books():
            if b == (book or "").upper():
                return root
        return None

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"review dashboard on 0.0.0.0:{args.port}")
    srv.serve_forever()
