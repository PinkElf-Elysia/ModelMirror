import json

from server.model_router.rpg_progress import TextPreview


def test_fragmented_preview_and_split_credentials():
    preview = TextPreview("fixture", ["secret-123"])
    def event(content):
        return ("data: " + json.dumps({"model": "fixture", "choices": [{"delta": {"content": content}}]},
                                      ensure_ascii=False) + "\r\n\r\n").encode()
    for byte in event("分片输出剧情。" * 5 + "secret-"):
        preview.feed(bytes([byte]))
    assert preview.visible().startswith("分片输出剧情。")
    assert "secret" not in preview.visible()
    preview.feed(event("123"))
    assert preview.visible() == ""
    assert preview.disabled
