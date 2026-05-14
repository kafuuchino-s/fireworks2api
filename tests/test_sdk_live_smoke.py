from __future__ import annotations

from types import SimpleNamespace

import scripts.sdk_live_smoke as smoke


def test_skips_cleanly_when_sdks_missing(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "load_sdk", lambda name: smoke.SDKLoad(None, f"missing {name}"))
    monkeypatch.setenv("FIREWORKS2API_BASE_URL", "http://testserver")
    monkeypatch.setenv("FIREWORKS2API_PROXY_KEY", "sk-test")
    monkeypatch.setenv("FIREWORKS2API_CHAT_MODEL", "kimi-k2.6")
    monkeypatch.setenv("FIREWORKS2API_RESPONSES_MODEL", "kimi-k2.6")
    monkeypatch.setenv("FIREWORKS2API_MESSAGES_MODEL", "kimi-k2.6")
    assert smoke.main() == 0
    out = capsys.readouterr().out
    assert "[skip] openai sdk" in out
    assert "[skip] anthropic sdk" in out


def test_runs_with_mocked_openai_and_anthropic(monkeypatch, capsys):
    class FakeChat:
        def create(self, **kwargs):
            assert kwargs["model"] == "kimi-k2.6"
            return {"id": "chatcmpl-1", "object": "chat.completion"}

    class FakeResponses:
        def create(self, **kwargs):
            assert kwargs["model"] == "kimi-k2.6"
            return {"id": "resp-1", "object": "response"}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeChat())
            self.responses = FakeResponses()

    class FakeMessages:
        def create(self, **kwargs):
            assert kwargs["model"] == "kimi-k2.6"
            return {"id": "msg-1", "type": "message"}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

    monkeypatch.setattr(smoke, "load_sdk", lambda name: smoke.SDKLoad(SimpleNamespace(OpenAI=FakeOpenAI, Anthropic=FakeAnthropic)))
    monkeypatch.setattr(smoke, "_client", lambda base_url, proxy_key: SimpleNamespace(close=lambda: None))
    monkeypatch.setenv("FIREWORKS2API_BASE_URL", "http://testserver")
    monkeypatch.setenv("FIREWORKS2API_PROXY_KEY", "sk-test")
    monkeypatch.setenv("FIREWORKS2API_CHAT_MODEL", "kimi-k2.6")
    monkeypatch.setenv("FIREWORKS2API_RESPONSES_MODEL", "kimi-k2.6")
    monkeypatch.setenv("FIREWORKS2API_MESSAGES_MODEL", "kimi-k2.6")
    assert smoke.main() == 0
    out = capsys.readouterr().out
    assert "[ok] openai chat completions" in out
    assert "[ok] openai responses" in out
    assert "[ok] anthropic messages" in out
