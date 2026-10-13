"""A stand-in decision endpoint (the /decisions protocol the classifier speaks), on a local port.

`answer(state, questions) -> answers` is set per test; the default says yes (0.95) to every yes/no
question and picks each choice question's first option with 0.95 confidence. Requests are kept in
`requests` so a test can see what the kernel asked.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def default_answer(state, questions):
    out = {}
    for qid, q in questions.items():
        if q["type"] == "noul":
            out[qid] = {"type": "noul", "noul": 0.95}
        else:
            first = next(iter(q["criteria"]))
            out[qid] = {"type": "choice", "choice": first, "confidence": 0.95,
                        "probabilities": {k: (0.95 if k == first else 0.05 / (len(q["criteria"]) - 1))
                                          for k in q["criteria"]}}
    return out


class FakeDecisions:
    def __init__(self):
        self.answer = default_answer
        self.requests = []
        self.status = 200
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({**body, "auth": self.headers.get("Authorization")})
                if fake.status != 200:
                    self.send_response(fake.status)
                    self.end_headers()
                    self.wfile.write(b'{"error": {"message": "down"}}')
                    return
                data = json.dumps({"model": body["model"], "answers": fake.answer(body["state"], body["questions"]),
                                   "usage": {"input_tokens": 1, "output_tokens": 0, "cost": 0}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/decisions"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def choose(self, **picks):
        """Answer choice questions by id with the given option (0.95); yes/no questions with 0.95, or a
        float given by id (e.g. external=0.9)."""
        def answer(state, questions):
            out = default_answer(state, questions)
            for qid, v in picks.items():
                if qid not in questions:
                    continue
                if isinstance(v, float):
                    out[qid] = {"type": "noul", "noul": v}
                else:
                    out[qid] = {"type": "choice", "choice": v, "confidence": 0.95, "probabilities": {v: 0.95}}
            return out
        self.answer = answer
