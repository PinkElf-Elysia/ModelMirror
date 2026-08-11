from __future__ import annotations

import base64
import json

from server.file_assets.output_media import ChatMediaCapture


def _event(candidate: dict[str, object]) -> str:
    return "data: " + json.dumps({"choices": [{"delta": candidate}]})


def test_capture_accepts_embedded_bytes_but_never_remote_urls() -> None:
    png = b"\x89PNG\r\n\x1a\npublic"
    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    capture = ChatMediaCapture()
    capture.consume_line(
        _event(
            {
                "images": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "image_url", "image_url": {"url": "https://example.invalid/a.png"}},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]
            }
        )
    )
    items = capture.items()
    assert len(items) == 1
    assert items[0].format_id == "png"
    assert items[0].content == png


def test_capture_joins_bounded_native_audio_chunks_and_drops_invalid_stream() -> None:
    capture = ChatMediaCapture()
    capture.consume_line(_event({"audio": {"data": base64.b64encode(b"one").decode("ascii")}}))
    capture.consume_line(_event({"audio": {"data": base64.b64encode(b"two").decode("ascii")}}))
    audio = capture.items(audio_format="mp3")
    assert len(audio) == 1
    assert audio[0].format_id == "mp3"
    assert audio[0].content == b"onetwo"

    invalid = ChatMediaCapture()
    invalid.consume_line(_event({"audio": {"data": "not base64"}}))
    assert invalid.items(audio_format="mp3") == ()
