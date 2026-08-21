from __future__ import annotations

import io
import json
import subprocess
import sys

from fecompiler.runtime.server import RuntimeServer
from fecompiler.runtime.stdio_server import run_stdio_server
from fecompiler.runtime.transport import ContentLengthDecoder, encode_content_length_frame


def _rpc_request(request_id: int, method: str, params: dict | None = None) -> bytes:
    request = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    return encode_content_length_frame(json.dumps(request))


def _decode_frames(raw: bytes) -> list[dict]:
    decoder = ContentLengthDecoder()
    return [json.loads(payload) for payload in decoder.feed(raw)]


def test_stdio_server_handles_multiple_framed_requests() -> None:
    input_stream = io.BytesIO(
        _rpc_request(1, "rpc.hello", {"version": 1})
        + _rpc_request(2, "rpc.shutdown"),
    )
    output_stream = io.BytesIO()

    assert run_stdio_server(input_stream, output_stream, server=RuntimeServer()) == 0
    responses = _decode_frames(output_stream.getvalue())
    assert [response["id"] for response in responses] == [1, 2]
    assert responses[0]["result"]["version"] == 1
    assert responses[1]["result"] == {"ok": True}


def test_stdio_server_writes_only_protocol_frames_when_handler_prints(capsys) -> None:
    server = RuntimeServer()

    def noisy(_params):
        print("business output")
        return {"ok": True}

    server._handlers["test.noisy"] = noisy
    input_stream = io.BytesIO(_rpc_request(1, "test.noisy"))
    output_stream = io.BytesIO()

    assert run_stdio_server(input_stream, output_stream, server=server) == 0
    assert _decode_frames(output_stream.getvalue()) == [
        {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}},
    ]
    assert "business output" in capsys.readouterr().err


def test_rpc_stdio_subprocess_smoke() -> None:
    requests = _rpc_request(1, "rpc.hello", {"version": 1}) + _rpc_request(
        2,
        "rpc.shutdown",
    )
    completed = subprocess.run(
        [sys.executable, "-m", "fecompiler.cli.main", "rpc", "serve", "--stdio"],
        input=requests,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    responses = _decode_frames(completed.stdout)
    assert len(responses) == 2
    assert responses[0]["result"]["version"] == 1
    assert responses[1]["result"] == {"ok": True}


def test_rpc_stdio_keeps_runtime_notifications_on_protocol_stdout() -> None:
    script = r"""
import sys

from fecompiler.runtime.server import RuntimeServer
from fecompiler.runtime.stdio_server import run_stdio_server

server = RuntimeServer()

def notify(_params):
    server._emit_runtime_event({
        "type": "event",
        "phase": "completed",
        "cmd": "rtl2gds",
        "data": {"step": "prepare", "state": "Success"},
    })
    return {"ok": True}

server._handlers["test.notify"] = notify
raise SystemExit(run_stdio_server(sys.stdin.buffer, sys.stdout.buffer, server=server))
"""
    requests = _rpc_request(1, "test.notify") + _rpc_request(2, "rpc.shutdown")

    completed = subprocess.run(
        [sys.executable, "-c", script],
        input=requests,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    frames = _decode_frames(completed.stdout)
    assert frames[0] == {
        "jsonrpc": "2.0",
        "method": "runtime.event",
        "params": {
            "type": "event",
            "phase": "completed",
            "cmd": "rtl2gds",
            "data": {"step": "prepare", "state": "Success"},
        },
    }
    assert frames[1] == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    assert frames[2] == {"jsonrpc": "2.0", "id": 2, "result": {"ok": True}}
    assert b"runtime.event" not in completed.stderr
