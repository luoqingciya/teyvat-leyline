"""本地服务测试：token 鉴权、配置恢复。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from teyvat_leyline.server import Server, create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # 配置/历史/断点全部落在临时目录
    server = Server()
    app = create_app(server)
    with TestClient(app) as c:
        yield c, server
    server.engine.shutdown()


def _h(server) -> dict:
    return {"X-Teyvat-Token": server.token}


def test_api_requires_token(client) -> None:
    c, server = client
    assert c.get("/api/config").status_code == 401
    assert c.get("/api/tasks").status_code == 401
    assert c.post("/api/tasks", json={"url": "http://example.com/f.zip"}).status_code == 401

    assert c.get("/api/config", headers=_h(server)).status_code == 200
    assert c.get("/api/tasks", headers=_h(server)).status_code == 200


def test_wrong_token_rejected(client) -> None:
    c, _ = client
    r = c.get("/api/config", headers={"X-Teyvat-Token": "deadbeef"})
    assert r.status_code == 401


def test_ws_rejects_missing_token(client) -> None:
    from fastapi import WebSocketDisconnect

    c, _ = client
    with pytest.raises(WebSocketDisconnect), c.websocket_connect("/api/ws"):
        pass  # 未带 token 应在握手后立即被关闭


def test_ws_accepts_valid_token(client) -> None:
    c, server = client
    with c.websocket_connect(f"/api/ws?token={server.token}"):
        pass


def test_save_dir_restored_from_config(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "data"
    target.mkdir()
    (tmp_path / "teyvat-config.json").write_text(
        json.dumps({"saveDir": str(target), "numThreads": 3}), encoding="utf-8"
    )

    server = Server()
    try:
        assert server.get_config()["saveDir"] == str(target)
        assert server.engine.save_dir == str(target)
        # 历史文件路径应随保存目录联动
        assert server.engine._history_file == target / "teyvat-history.json"
        assert server.engine.num_threads == 3
    finally:
        server.engine.shutdown()


def test_save_dir_ignored_when_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "teyvat-config.json").write_text(
        json.dumps({"saveDir": str(tmp_path / "nope")}), encoding="utf-8"
    )
    server = Server()
    try:
        assert server.get_config()["saveDir"] == str(tmp_path)
    finally:
        server.engine.shutdown()
