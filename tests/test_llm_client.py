"""Tests del cliente Anthropic (regresión B6).

Dos defectos:
  1. ``chat()``/``stream()`` no pasaban ``temperature`` a Anthropic
     (OpenAI/Azure sí) → comportamiento divergente por proveedor.
  2. La agregación de texto usaba ``content = block.text`` (sobrescribe);
     con varios bloques de texto solo sobrevivía el último.
"""

import pytest

from geo_copilot.core.llm_client import AnthropicClient, LLMMessage


class _Block:
    def __init__(self, type_, text=None):
        self.type = type_
        self.text = text


class _Usage:
    input_tokens = 10
    output_tokens = 5


class _Response:
    def __init__(self, blocks):
        self.content = blocks
        self.usage = _Usage()


class _FakeMessages:
    def __init__(self, response):
        self._response = response
        self.captured_kwargs = None

    async def create(self, **kwargs):
        self.captured_kwargs = kwargs
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.messages = _FakeMessages(response)


@pytest.mark.asyncio
async def test_anthropic_chat_passes_temperature(monkeypatch):
    client = AnthropicClient(api_key="x", model="claude-test")
    fake = _FakeClient(_Response([_Block("text", "hola")]))

    async def _fake_get_client():
        return fake

    monkeypatch.setattr(client, "_get_client", _fake_get_client)

    await client.chat([LLMMessage(role="user", content="hi")], temperature=0.7)

    assert fake.messages.captured_kwargs["temperature"] == 0.7


@pytest.mark.asyncio
async def test_anthropic_chat_concatenates_text_blocks(monkeypatch):
    client = AnthropicClient(api_key="x", model="claude-test")
    fake = _FakeClient(
        _Response([_Block("text", "parte 1 "), _Block("text", "parte 2")])
    )

    async def _fake_get_client():
        return fake

    monkeypatch.setattr(client, "_get_client", _fake_get_client)

    result = await client.chat([LLMMessage(role="user", content="hi")])

    assert result.content == "parte 1 parte 2"


@pytest.mark.asyncio
async def test_anthropic_chat_default_temperature(monkeypatch):
    client = AnthropicClient(api_key="x", model="claude-test")
    fake = _FakeClient(_Response([_Block("text", "ok")]))

    async def _fake_get_client():
        return fake

    monkeypatch.setattr(client, "_get_client", _fake_get_client)

    await client.chat([LLMMessage(role="user", content="hi")])

    # default declarado en la firma
    assert fake.messages.captured_kwargs["temperature"] == 0.1
