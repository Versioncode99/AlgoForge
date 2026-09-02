from __future__ import annotations

import json

import httpx
from forge_api.model_gateway import OmniRouteClient, _key_from_file


def test_key_file_ignores_dashboard_password_and_reads_explicit_api_key(tmp_path) -> None:
    path = tmp_path / "omni route key.txt"
    path.write_text("Dashboard password: do-not-use\nOmniRoute API key: sk-test-only\n")
    assert _key_from_file(path) == "sk-test-only"


def test_status_and_chat_use_openai_compatible_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "auto"}, {"id": "auto/coding"}]})
        body = json.loads(request.content)
        assert body["model"] == "auto"
        return httpx.Response(
            200,
            headers={"x-omniroute-decision": "auto:test"},
            json={
                "model": "provider/test",
                "choices": [{"message": {"content": "Grounded answer"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    client = OmniRouteClient(transport=httpx.MockTransport(handler))
    status = client.status()
    assert status["connected"] is True
    assert status["model_count"] == 2
    assert "value" not in status

    answer = client.chat(model="auto", system="system", prompt="question")
    assert answer["answer"] == "Grounded answer"
    assert answer["route"] == "auto:test"
