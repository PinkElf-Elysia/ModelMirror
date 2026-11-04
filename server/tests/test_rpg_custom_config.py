from server.tests.test_rpg_bridge import HEADERS, payload, setup
from server.model_router import rpg_bridge as bridge
import json


def test_custom_story_preserves_both_hashes_and_dispatches_prefill(setup):
    client, stable, _, _, calls, _ = setup
    source = payload(client)
    effective = [source["messages"][0], {"role": "user", "content": "wrapped input"},
                 {"role": "assistant", "content": "prefilled"}]
    result = client.post("/api/rpg/v1/chat/completions", headers=HEADERS,
                         json={**source, "customStoryMessages": effective})
    assert result.status_code == 200, result.text
    receipt = result.json()["receipt"]
    assert receipt["requestHash"] == bridge.digest(source)
    assert receipt["effectiveRequestHash"] == bridge.digest({**source, "messages": effective})
    assert calls[0]["messages"] == effective
    folder = stable.repository.storage_dir / "rpg-bridge-requests"
    assert json.loads(next(folder.glob("*.json")).read_text())["effectiveRequestHash"] == receipt["effectiveRequestHash"]


def test_custom_story_rejects_replaced_card_prompt_before_dispatch(setup):
    client, _, _, _, calls, _ = setup
    source = payload(client)
    bad = [{"role": "system", "content": "replace card"}, source["messages"][-1]]
    response = client.post("/api/rpg/v1/chat/completions", headers=HEADERS,
                           json={**source, "customStoryMessages": bad})
    assert response.status_code == 422
    assert not calls
