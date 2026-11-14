"""Regression tests for the defects found by the review of PR #2: each one failed before its fix."""

from __future__ import annotations

import json
import pathlib
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from frontier import agent as agent_mod
from frontier import budget, gateway, launcher
from frontier.config import load_config


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def run(cfg, tmp_path):
    return agent_mod.Run(cfg, workspace=tmp_path / "ws", log_dir=tmp_path / "log")


# ---------------------------------------------------------------- gateway


class _NoUsage(BaseHTTPRequestHandler):
    """A 2xx answer whose usage the gateway cannot read."""

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        data = b'{"output": "hi"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_gateway_charges_the_worst_case_when_usage_is_unknown(tmp_path):
    up = ThreadingHTTPServer(("127.0.0.1", 0), _NoUsage)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    b = budget.Budget(tmp_path)
    gw = gateway.Gateway(("127.0.0.1", 0), b, {"zhipu": (f"http://127.0.0.1:{up.server_address[1]}", "zhipu")})
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    body = json.dumps({"model": "glm-5.3", "messages": [], "max_tokens": 1000}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{gw.server_address[1]}/zhipu/api/paas/v4/chat/completions",
                                 data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()
    gw.shutdown()
    up.shutdown()
    (e,) = b.llm_entries()
    assert e["frontier.cost.usd"] == budget.worst_case_usd("zhipu", "glm-5.3", len(body), 1000)
    assert e["frontier.price_exact"] is False and b.reserved() == 0


def test_gateway_reserves_at_the_dearest_mode(cfg):
    dearest = max(float(m.get("price_x", 1)) for m in cfg["providers"]["openai"].get("modes", {}).values())
    assert dearest > 1
    body = json.dumps({"model": "gpt-6-astra", "input": "x", "max_output_tokens": 1000}).encode()
    standard = budget.worst_case_usd("openai", "gpt-6-astra", len(body), 1000)
    assert gateway.worst_case(cfg, "openai", "/v1/responses", body) == pytest.approx(standard * dearest)


# ---------------------------------------------------------------- launcher


def test_a_session_is_killed_at_the_deadline(tmp_path):
    t0 = time.time()
    with open(tmp_path / "log", "w") as f:
        code = launcher.run_until(["bash", "-c", "sleep 30 & echo $! > child; wait"], 1, cwd=tmp_path, stdout=f)
    assert time.time() - t0 < 10 and code == launcher.DEADLINE_EXIT
    child = int((tmp_path / "child").read_text())
    time.sleep(0.2)
    stat = pathlib.Path(f"/proc/{child}/stat")  # the harness's own children are killed too (gone, or a zombie)
    assert not stat.exists() or stat.read_text().rsplit(")", 1)[1].split()[0] == "Z"
