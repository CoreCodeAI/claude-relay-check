#!/usr/bin/env python3
"""Tests for check-relay.py against local fake relays."""
import hashlib
import hmac
import importlib.util
import io
import json
import re
import threading
import unittest
from collections import Counter
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check-relay.py"
spec = importlib.util.spec_from_file_location("check_relay", SCRIPT)
check_relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_relay)
check_relay.time.sleep = lambda seconds: None


class Upstream:
    """Anthropic's behaviour as far as the checks see it."""
    secret = b"upstream"
    sign_thinking = True
    verify_signatures = True
    cache = True
    same_account = True

    def __init__(self):
        self.cached = set()

    def sign(self, text):
        return hmac.new(self.secret, text.encode(), hashlib.sha256).hexdigest() * 2

    def respond(self, body):
        messages = body["messages"]
        for message in messages:
            for block in message["content"] if isinstance(message["content"], list) else []:
                if block.get("type") == "thinking" and self.verify_signatures and block["signature"] != self.sign(block["thinking"]):
                    return 400, {"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid signature in thinking block"}}
        usage = {"input_tokens": 12, "output_tokens": 3}
        system = body.get("system")
        if system and repeats_lines(system[0]["text"]):
            # Sonnet 5.5 on corecode.live, 2026-11-19: ten or more near-identical system lines got a 400.
            return 400, {"type": "error", "error": {"type": "invalid_request_error", "message": "Error"}}
        if system and self.cache:
            prefix = system[0]["text"]
            tokens = len(prefix) // 4
            if prefix in self.cached and self.same_account:
                usage.update(cache_read_input_tokens=tokens, cache_creation_input_tokens=0)
            else:
                usage.update(cache_read_input_tokens=0, cache_creation_input_tokens=tokens)
                self.cached.add(prefix)
        content = [{"type": "text", "text": "ok"}]
        if body.get("thinking") and len(messages) == 1:
            thought = "The user wants the weather; call the tool."
            content = [{"type": "thinking", "thinking": thought, "signature": self.sign(thought) if self.sign_thinking else ""},
                       {"type": "tool_use", "id": "toolu_01", "name": "get_weather", "input": {"city": "Paris"}}]
        return 200, {"id": "msg_01abc", "type": "message", "role": "assistant", "model": body["model"],
                     "content": content, "usage": usage}


def repeats_lines(text):
    """Ten or more lines that differ only in a number."""
    counts = Counter(re.sub(r"\d+", "#", line) for line in text.splitlines())
    return max(counts.values()) >= 10


def serve(upstream):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            status, payload = upstream.respond(body)
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class CheckRelayTest(unittest.TestCase):
    def run_against(self, upstream):
        server = serve(upstream)
        self.addCleanup(server.shutdown)
        out = io.StringIO()
        with redirect_stdout(out):
            code = check_relay.main(["--base-url", f"http://127.0.0.1:{server.server_port}/", "--key", "sk-test"])
        lines = {line.split()[1]: line.split()[0] for line in out.getvalue().splitlines() if line.startswith("[")}
        return code, lines

    def test_genuine_upstream_passes(self):
        code, lines = self.run_against(Upstream())
        self.assertEqual(lines, {"shape": "[PASS]", "cache": "[PASS]", "signature": "[PASS]"})
        self.assertEqual(code, 0)

    def test_unverified_signature_fails(self):
        upstream = Upstream()
        upstream.verify_signatures = False
        code, lines = self.run_against(upstream)
        self.assertEqual(lines["signature"], "[FAIL]")
        self.assertEqual(code, 1)

    def test_missing_signature_fails(self):
        upstream = Upstream()
        upstream.sign_thinking = False
        self.assertEqual(self.run_against(upstream)[1]["signature"], "[FAIL]")

    def test_no_cache_fails(self):
        upstream = Upstream()
        upstream.cache = False
        code, lines = self.run_against(upstream)
        self.assertEqual(lines["cache"], "[FAIL]")
        self.assertEqual(code, 1)

    def test_cache_on_another_account_warns(self):
        upstream = Upstream()
        upstream.same_account = False
        code, lines = self.run_against(upstream)
        self.assertEqual(lines["cache"], "[WARN]")
        self.assertEqual(code, 0)

    def test_non_anthropic_shape_fails(self):
        upstream = Upstream()
        upstream.respond = lambda body: (200, {"id": "chatcmpl-1", "object": "chat.completion", "choices": []})
        code, lines = self.run_against(upstream)
        self.assertEqual(lines["shape"], "[FAIL]")
        self.assertEqual(code, 1)

    def test_falls_back_to_budget_thinking_when_adaptive_is_refused(self):
        upstream = Upstream()
        respond = upstream.respond
        seen = []

        def old_model(body):
            seen.append(body.get("thinking", {}).get("type"))
            if body.get("thinking", {}).get("type") == "adaptive":
                return 400, {"type": "error", "error": {"type": "invalid_request_error", "message": "adaptive thinking is not supported"}}
            return respond(body)
        upstream.respond = old_model
        code, lines = self.run_against(upstream)
        self.assertEqual(lines["signature"], "[PASS]")
        self.assertEqual(seen.count("adaptive"), 1)
        self.assertGreaterEqual(seen.count("enabled"), 3)  # first call and both replays

    def test_request_error_is_not_a_verdict(self):
        upstream = Upstream()
        upstream.respond = lambda body: (401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid key"}})
        code, lines = self.run_against(upstream)
        self.assertEqual(lines["shape"], "[ERROR]")
        self.assertEqual(lines["signature"], "[ERROR]")  # a 401 or 403 is not "the model refused thinking"
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
