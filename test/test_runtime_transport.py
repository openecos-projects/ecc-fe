from __future__ import annotations

import pytest

from fecompiler.runtime.transport import (
    ContentLengthDecoder,
    TransportError,
    encode_content_length_frame,
)


def test_content_length_frame_round_trip_in_chunks() -> None:
    decoder = ContentLengthDecoder()
    frame = encode_content_length_frame('{"jsonrpc":"2.0"}')

    assert decoder.feed(frame[:10]) == []
    assert decoder.feed(frame[10:]) == [b'{"jsonrpc":"2.0"}']


def test_content_length_decoder_handles_multiple_frames() -> None:
    decoder = ContentLengthDecoder()
    frames = encode_content_length_frame("one") + encode_content_length_frame("two")

    assert decoder.feed(frames) == [b"one", b"two"]


def test_content_length_decoder_rejects_invalid_length() -> None:
    decoder = ContentLengthDecoder()

    with pytest.raises(TransportError, match="integer"):
        decoder.feed(b"Content-Length: nope\r\n\r\n")


def test_content_length_decoder_rejects_oversized_payload() -> None:
    decoder = ContentLengthDecoder(max_payload_size=4)

    with pytest.raises(TransportError, match="maximum"):
        decoder.feed(b"Content-Length: 5\r\n\r\n")
