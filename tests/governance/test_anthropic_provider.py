from types import SimpleNamespace

from tollgate.governance.runtime.anthropic_provider import AnthropicMessagesProvider, AnthropicPricing


def test_anthropic_messages_usage_and_cost_mapping():
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Tollgate Anthropic passed")],
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_creation_input_tokens=10,
            cache_read_input_tokens=5,
        ),
    )

    class Messages:
        def create(self, **kwargs):
            assert kwargs["model"] == "claude-test"
            assert kwargs["max_tokens"] == 40
            assert kwargs["messages"] == [{"role": "user", "content": "pilot payload"}]
            assert kwargs["system"] == "be exact"
            return response

    client = SimpleNamespace(messages=Messages())
    provider = AnthropicMessagesProvider(
        pricing=AnthropicPricing(
            input_per_million_usd=3,
            output_per_million_usd=15,
            cache_write_per_million_usd=3.75,
            cache_read_per_million_usd=0.30,
        ),
        client=client,
    )

    result = provider.complete(
        model="claude-test",
        payload="pilot payload",
        max_output_tokens=40,
        instructions="be exact",
    )

    expected = (100 * 3 + 20 * 15 + 10 * 3.75 + 5 * 0.30) / 1_000_000
    assert result.content == "Tollgate Anthropic passed"
    assert result.input_tokens == 115
    assert result.output_tokens == 20
    assert result.cost_usd == expected
    assert result.raw is response
