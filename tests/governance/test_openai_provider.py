from types import SimpleNamespace

from context_ledger.governance.runtime.openai_provider import OpenAIPricing, OpenAIResponsesProvider


class FakeResponses:
    def __init__(self) -> None:
        self.last_request = None

    def create(self, **kwargs):
        self.last_request = kwargs
        return SimpleNamespace(
            output_text="pilot ok",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=500),
            _request_id="req_test",
        )


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


def test_openai_responses_adapter_uses_measured_usage_and_pricing():
    client = FakeClient()
    provider = OpenAIResponsesProvider(
        client=client,
        pricing=OpenAIPricing(input_per_million_usd=2.0, output_per_million_usd=8.0),
    )

    response = provider.complete(
        model="pilot-model",
        payload="hello",
        instructions="be exact",
        max_output_tokens=25,
    )

    assert response.content == "pilot ok"
    assert response.input_tokens == 1000
    assert response.output_tokens == 500
    assert response.cost_usd == 0.006
    assert client.responses.last_request == {
        "model": "pilot-model",
        "input": "hello",
        "max_output_tokens": 25,
        "instructions": "be exact",
    }
