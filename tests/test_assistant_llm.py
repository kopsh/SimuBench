"""The assistant's LLM settings and protocols (#872, D66): both wire formats through a custom
base_url with tool calls and streaming (a fake service on an in-memory transport, no network),
the connectivity test, where settings come from, the address rules, the write-only key, and the
HTTP routes with the event stream."""

from __future__ import annotations

import json
import logging
import os
import stat
import sys

import httpx2
import pytest
from fastapi.testclient import TestClient
from tests.helpers import ScriptedLLM

from simubench.assistant.config import AssistantSettings, ConfigError, check_base_url
from simubench.assistant.llm import build_model, check_connection
from simubench.assistant.service import AssistantService
from simubench.models import api as M
from simubench.server.app import create_app
from simubench.server.manager import SessionManager

BENCH = "hello.bench.yaml"
KEY = "sk-secret-key-123"


class FakeLLM:
    """A tool-calling LLM service speaking OpenAI Chat Completions or Anthropic Messages, streamed:
    the first request answers with a read_bench call, the next with text."""

    def __init__(self, protocol: str, status: int = 200, call_tool: bool = True) -> None:
        self.protocol, self.status, self.call_tool = protocol, status, call_tool
        self.requests: list[httpx2.Request] = []

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self.handle))

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx2.Response(self.status, json={"error": {"message": f"bad key {KEY}", "type": "auth"}})
        body = json.loads(request.content)
        tool = self.call_tool and len(self.requests) % 2 == 1
        name = body["tools"][0]["function"]["name"] if self.protocol == "openai" else body["tools"][0]["name"]
        if any(t.get("function", t).get("name") == "read_bench" for t in body.get("tools", [])):
            name = "read_bench"
        text = self._openai(tool, name) if self.protocol == "openai" else self._anthropic(tool, name)
        return httpx2.Response(200, text=text, headers={"content-type": "text/event-stream"})

    @staticmethod
    def _openai(tool: bool, name: str) -> str:
        base = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m"}
        if tool:
            deltas = [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "c1",
                            "type": "function",
                            "function": {"name": name, "arguments": ""},
                        }
                    ],
                },
                {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]},
            ]
            finish = "tool_calls"
        else:
            deltas = [{"role": "assistant", "content": "读到了"}, {"content": "测试台。"}]
            finish = "stop"
        chunks = [base | {"choices": [{"index": 0, "delta": d}]} for d in deltas]
        chunks.append(base | {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
        return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    @staticmethod
    def _anthropic(tool: bool, name: str) -> str:
        def ev(t: str, d: dict) -> str:
            return f"event: {t}\ndata: {json.dumps(d | {'type': t})}\n\n"

        msg = {
            "id": "m",
            "type": "message",
            "role": "assistant",
            "model": "m",
            "content": [],
            "stop_reason": None,
            "usage": {"input_tokens": 10, "output_tokens": 0},
        }
        out = ev("message_start", {"message": msg})
        if tool:
            out += ev(
                "content_block_start",
                {"index": 0, "content_block": {"type": "tool_use", "id": "t1", "name": name, "input": {}}},
            )
            out += ev(
                "content_block_delta",
                {"index": 0, "delta": {"type": "input_json_delta", "partial_json": "{}"}},
            )
            stop = "tool_use"
        else:
            out += ev("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}})
            out += ev("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "读到了"}})
            out += ev(
                "content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "测试台。"}}
            )
            stop = "end_turn"
        out += ev("content_block_stop", {"index": 0})
        out += ev("message_delta", {"delta": {"stop_reason": stop}, "usage": {"output_tokens": 5}})
        return out + ev("message_stop", {})


def env(protocol: str, base_url: str) -> dict[str, str]:
    return {
        "SIMUBENCH_LLM_PROTOCOL": protocol,
        "SIMUBENCH_LLM_BASE_URL": base_url,
        "SIMUBENCH_LLM_MODEL": "private-model",
        "SIMUBENCH_LLM_API_KEY": KEY,
    }


@pytest.fixture
def manager(tmp_path):
    m = SessionManager(tmp_path, scpi_base_port=None, hislip_port=None, link_port=None)
    m.files.create_hello("ui")
    return m


@pytest.mark.parametrize(
    ("protocol", "base_url", "path"),
    [
        ("openai", "http://llm.internal:8000/v1", "/v1/chat/completions"),
        ("anthropic", "http://10.0.0.7:9000/gateway", "/gateway/v1/messages"),
    ],
)
async def test_both_protocols_call_tools_and_stream_through_a_custom_address(
    manager, protocol, base_url, path, caplog
):
    fake = FakeLLM(protocol)
    service = AssistantService(
        manager, model_factory=lambda s: build_model(s, fake.client()), env=env(protocol, base_url)
    )
    await service.send(BENCH, "读一下测试台")
    await service.tasks[BENCH]
    t = service.conversation(BENCH).turns[-1]
    assert t.status == "done", t.stop_reason
    assert t.reply == "读到了测试台。"
    assert [s.action for s in t.steps] == ["read_bench"] and t.steps[0].ok
    texts = [e.text for e in service.logs[BENCH].events if e.type == "text"]
    assert len(texts) >= 2  # streamed in pieces
    first = fake.requests[0]
    assert (
        str(first.url).startswith(base_url.split("/", 3)[0] + "//" + base_url.split("/")[2])
        and first.url.path == path
    )
    assert json.loads(first.content)["stream"] is True
    assert KEY in first.headers.get("authorization", "") + first.headers.get("x-api-key", "")
    # the tool result went back to the model in the protocol's own shape
    second = json.loads(fake.requests[1].content)["messages"]
    assert any(m["role"] == ("tool" if protocol == "openai" else "user") for m in second)


async def test_connection_test_reports_in_words_and_never_the_key(manager, caplog):
    settings = AssistantSettings(manager.workspace, env("openai", "http://llm.internal/v1")).current()
    ok = await check_connection(settings, FakeLLM("openai").client())
    assert ok.ok and "private-model" in ok.message and ok.latency_ms is not None
    no_tools = await check_connection(settings, FakeLLM("openai", call_tool=False).client())
    assert not no_tools.ok and "tool calling" in no_tools.message
    with caplog.at_level(logging.DEBUG):
        denied = await check_connection(settings, FakeLLM("openai", status=401).client())
    assert not denied.ok and "401" in denied.message and "API key" in denied.message
    assert KEY not in denied.message and KEY not in caplog.text
    unset = await check_connection(AssistantSettings(manager.workspace, {}).current())
    assert not unset.ok and "还没有配置" in unset.message
    assert KEY not in repr(settings)


def test_settings_come_from_the_server_and_the_page_overrides_them(tmp_path):
    (tmp_path / ".simubench").mkdir()
    (tmp_path / ".simubench" / "settings.json").write_text(
        json.dumps(
            {"assistant": {"base_url": "http://from-file/v1", "model": "file-model", "context_tokens": 8000}}
        ),
        encoding="utf-8",
    )
    s = AssistantSettings(tmp_path, {"SIMUBENCH_LLM_MODEL": "env-model"})
    view = s.view()
    assert (view.base_url, view.model, view.context_tokens, view.configured) == (
        "http://from-file/v1",
        "env-model",
        8000,
        True,
    )
    assert not view.api_key_set and view.overridden == []
    view = s.update(M.AssistantConfigUpdate(protocol="anthropic", model="page-model", api_key=KEY))
    assert (view.protocol, view.model, view.api_key_set) == ("anthropic", "page-model", True)
    assert view.overridden == ["api_key", "model", "protocol"]
    assert KEY not in view.model_dump_json()
    stored = tmp_path / ".simubench" / "assistant.json"
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(stored).st_mode) == 0o600
    assert s.update(M.AssistantConfigUpdate(timeout_s=5)).api_key_set  # a key left out is kept
    assert not s.update(M.AssistantConfigUpdate(api_key="")).api_key_set
    assert s.update(M.AssistantConfigUpdate(reset=True)).model == "env-model"


def test_the_address_is_http_and_never_simubench_itself(tmp_path):
    manager = SessionManager(tmp_path, scpi_base_port=5025, hislip_port=4880, link_port=4881)
    manager.web_port = 8765
    for bad in ("ftp://llm/v1", "llm.internal/v1", "http://u:p@llm/v1"):
        with pytest.raises(ConfigError, match=r"http|用户名"):
            check_base_url(bad, manager)
    for fine in ("http://127.0.0.1:11434/v1", "https://10.1.2.3/v1", "http://llm.corp.internal:8000/v1"):
        check_base_url(fine, manager)
    with pytest.raises(ConfigError):
        AssistantSettings(tmp_path, {}).update(
            M.AssistantConfigUpdate(base_url="http://127.0.0.1:8765"), manager
        )


@pytest.mark.parametrize(
    ("url", "port"),
    [
        pytest.param("http://127.0.0.1:8765/v1", 8765, id="plain"),
        pytest.param("http://127.1:8765/v1", 8765, id="two-part-shorthand"),
        pytest.param("http://2130706433:8765", 8765, id="decimal-integer"),
        pytest.param("http://0x7f.1:8765", 8765, id="hex-shorthand"),
        pytest.param("http://127.000.000.001:8765/v1", 8765, id="octal-parts"),
        pytest.param("http://localhost.:8765/v1", 8765, id="trailing-dot"),
        pytest.param("HTTP://LOCALHOST.:8765", 8765, id="upper-case"),
        pytest.param("http://localhost:5030", 5030, id="scpi-base"),
        pytest.param("http://[::1]:4880", 4880, id="ipv6-loopback"),
        pytest.param("http://[::ffff:127.0.0.1]:8765", 8765, id="ipv4-mapped"),
        pytest.param("http://127.0.0.1:4881", 4881, id="link-port"),
    ],
)
def test_the_service_is_never_reachable_under_an_alias_of_itself(url, port, tmp_path):
    manager = SessionManager(tmp_path, scpi_base_port=5025, hislip_port=4880, link_port=4881)
    manager.web_port = port
    with pytest.raises(ConfigError, match="自己的端口"):
        check_base_url(url, manager)


@pytest.mark.parametrize(
    ("url", "port"),
    [
        pytest.param("http://²:8765", 8765, id="superscript-host"),
        pytest.param("http://1²:8765", 8765, id="mixed-superscript"),
        pytest.param("http://²:5025", 5025, id="superscript-scpi-port"),
    ],
)
def test_a_hostname_with_unicode_digits_is_treated_as_a_name(url, port, tmp_path):
    """``²`` passes isdigit() but int() rejects it: the shorthand parser must say None and the
    address stays an ordinary host name, not an unhandled ValueError (#914)."""
    manager = SessionManager(tmp_path, scpi_base_port=5025, hislip_port=4880, link_port=4881)
    manager.web_port = port
    check_base_url(url, manager)  # no ConfigError, and no ValueError either


async def test_a_default_address_is_checked_before_a_turn_uses_it(manager):
    """A default from the environment pointing at the service itself refuses the turn in words
    (#901) — it is not only the settings page that checks."""
    manager.web_port = 8765
    service = AssistantService(
        manager,
        env={"SIMUBENCH_LLM_BASE_URL": "http://2130706433:8765/v1", "SIMUBENCH_LLM_MODEL": "m"},
    )
    with pytest.raises(ConfigError, match="自己的端口"):
        await service.send(BENCH, "你好")
    assert service.conversation(BENCH).turns == []
    with pytest.raises(ConfigError, match="自己的端口"):
        service.settings.update(M.AssistantConfigUpdate(base_url="http://127.1:8765"), manager)


def test_the_connection_test_reports_a_bad_default_in_words(client, manager):
    manager.web_port = 8765
    manager.assistant.settings = AssistantSettings(
        manager.workspace,
        {"SIMUBENCH_LLM_BASE_URL": "http://localhost.:8765/v1", "SIMUBENCH_LLM_MODEL": "m"},
    )
    result = client.post("/api/assistant/config/test").json()
    assert not result["ok"] and "自己的端口" in result["message"]
    refused = client.post("/api/assistant/messages", json={"bench": BENCH, "text": "你好"})
    assert refused.status_code == 422 and "自己的端口" in refused.json()["detail"]


@pytest.fixture
def client(manager):
    app = create_app(manager, token="t", web_dist=manager.workspace / "none")
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = "t"
        yield c


def test_routes_settings_conversation_and_event_stream(client, manager):
    # not configured: the panel shows the way to the settings page, a message is refused
    assert client.get("/api/assistant/config").json()["configured"] is False
    assert client.post("/api/assistant/messages", json={"bench": BENCH, "text": "hi"}).status_code == 422
    saved = client.put(
        "/api/assistant/config", json={"base_url": "http://llm.internal/v1", "model": "m", "api_key": KEY}
    )
    assert saved.status_code == 200 and saved.json()["api_key_set"] and KEY not in saved.text
    assert KEY not in client.get("/api/assistant/config").text
    assert client.put("/api/assistant/config", json={"base_url": "file:///etc/passwd"}).status_code == 422

    llm = ScriptedLLM([("read_bench", {})], "看过了。")
    manager.assistant.model_factory = llm.factory
    r = client.post("/api/assistant/messages", json={"bench": BENCH, "text": "看看"})
    assert r.status_code == 202
    turn_id = r.json()["id"]
    with client.stream("GET", "/api/assistant/events", params={"bench": BENCH, "turn": turn_id}) as stream:
        events = [json.loads(line[5:]) for line in stream.iter_lines() if line.startswith("data:")]
    assert events[-1]["type"] == "turn", [e["type"] for e in events]
    assert events[-1]["turn"]["status"] == "done"
    assert "".join(e["text"] for e in events if e["type"] == "text") == "看过了。"
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    conv = client.get("/api/assistant/conversation", params={"bench": BENCH}).json()
    assert [t["user"] for t in conv["turns"]] == ["看看"] and not conv["running"]
    assert client.get("/api/assistant/rollback", params={"bench": BENCH}).json()["turn_id"] is None
    assert client.post("/api/assistant/stop", json={"bench": BENCH}).status_code == 200
    assert client.get("/api/assistant/conversation", params={"bench": "../x.bench.yaml"}).status_code == 403
    # the diagnostics bundle carries no key either (logs, manifest, benches)
    import io
    import zipfile

    from simubench.ops import bundle

    with zipfile.ZipFile(io.BytesIO(bundle(manager, manager.workspace))) as z:
        assert all(KEY.encode() not in z.read(n) for n in z.namelist())
    assert client.delete("/api/assistant/conversation", params={"bench": BENCH}).status_code == 204
    assert client.get("/api/assistant/conversation", params={"bench": BENCH}).json()["turns"] == []
