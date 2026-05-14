from __future__ import annotations

from types import SimpleNamespace

import scripts.live_capability_smoke as smoke


def test_env_parsing_and_sanitized_env(monkeypatch):
    monkeypatch.setenv("FIREWORKS2API_CHAT_MODEL", "chat-a")
    monkeypatch.setenv("FIREWORKS2API_EMBEDDINGS_MODEL", "embed-a")
    monkeypatch.setenv("FIREWORKS2API_RERANK_MODEL", "rank-a")
    env_map = smoke.build_smoke_env("http://example.test")
    assert env_map["FIREWORKS2API_BASE_URL"] == "http://example.test"
    assert "FIREWORKS2API_EMBEDDINGS_MODEL" not in env_map
    assert "FIREWORKS2API_RERANK_MODEL" not in env_map


def test_write_results(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, "DATA_DIR", tmp_path)
    monkeypatch.setattr(smoke, "LATEST_JSON", tmp_path / "latest.json")
    monkeypatch.setattr(smoke, "CURRENT_ALIAS_JSON", tmp_path / "current-alias-matrix.json")
    monkeypatch.setattr(smoke, "CURRENT_ALIAS_MD", tmp_path / "current-alias-matrix.md")
    smoke.write_results({"current_aliases": {"chat": "pass"}})
    assert (tmp_path / "latest.json").exists()
    assert (tmp_path / "current-alias-matrix.json").exists()
    assert (tmp_path / "current-alias-matrix.md").read_text(encoding="utf-8").startswith("# Current Alias Matrix")


def test_reuse_branch_skips_server_start(monkeypatch):
    started = {"called": False}
    monkeypatch.setattr(smoke, "base_url_reachable", lambda base_url, timeout_seconds=2.0: True)
    monkeypatch.setattr(smoke, "start_local_server", lambda: started.__setitem__("called", True))
    monkeypatch.setattr(smoke, "run_script", lambda path, base_url: SimpleNamespace(returncode=0))
    monkeypatch.setattr(smoke, "write_results", lambda payload: None)
    assert smoke.main() == 0
    assert started["called"] is False


def test_cleanup_helper_terminates_process(tmp_path):
    proc = SimpleNamespace(poll=lambda: None, terminate=lambda: None, wait=lambda timeout: None, kill=lambda: None)
    handle = smoke.ServerHandle(process=proc, log_path=tmp_path / "log.txt")
    handle.log_path.write_text("x", encoding="utf-8")
    smoke.cleanup_server(handle)


def test_sanitized_env_excludes_embeddings_and_rerank(monkeypatch):
    monkeypatch.setenv("FIREWORKS2API_EMBEDDINGS_MODEL", "embed-a")
    monkeypatch.setenv("FIREWORKS2API_RERANK_MODEL", "rank-a")
    env_map = smoke.sanitized_subprocess_env("http://example.test")
    assert "FIREWORKS2API_EMBEDDINGS_MODEL" not in env_map
    assert "FIREWORKS2API_RERANK_MODEL" not in env_map
