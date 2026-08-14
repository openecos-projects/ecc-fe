from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import BinaryIO

from fecompiler.runtime.server import RuntimeServer
from fecompiler.runtime.transport import (
    ContentLengthDecoder,
    TransportError,
    encode_content_length_frame,
)


def run_stdio_server(
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    *,
    server: RuntimeServer | None = None,
) -> int:
    with _stable_protocol_output(output_stream) as protocol_output:
        return _serve_stdio(input_stream, protocol_output, server=server)


def _serve_stdio(
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    *,
    server: RuntimeServer | None = None,
) -> int:
    runtime_server = server or RuntimeServer()

    def write_notification(payload: dict) -> None:
        output_stream.write(
            encode_content_length_frame(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        output_stream.flush()

    runtime_server.set_notification_sink(write_notification)
    decoder = ContentLengthDecoder()

    while not runtime_server.should_exit:
        chunk = _read_chunk(input_stream)
        if not chunk:
            break
        try:
            messages = decoder.feed(chunk)
        except TransportError as exc:
            print(f"transport error: {exc}", file=sys.stderr)
            return 1

        for message in messages:
            response = runtime_server.dispatch(message)
            if response:
                output_stream.write(encode_content_length_frame(response))
                output_stream.flush()
            if runtime_server.should_exit:
                break

    return 0


@contextmanager
def _stable_protocol_output(output_stream: BinaryIO) -> Iterator[BinaryIO]:
    """Keep protocol frames on the original stdout while handlers redirect FD 1."""

    try:
        output_fd = output_stream.fileno()
        protocol_fd = os.dup(output_fd)
    except (AttributeError, OSError):
        yield output_stream
        return

    with os.fdopen(protocol_fd, "wb", buffering=0) as protocol_output:
        yield protocol_output


def _read_chunk(input_stream: BinaryIO) -> bytes:
    read1 = getattr(input_stream, "read1", None)
    if read1 is not None:
        return read1(8192)
    return input_stream.read(8192)


def main() -> int:
    return run_stdio_server(sys.stdin.buffer, sys.stdout.buffer)
