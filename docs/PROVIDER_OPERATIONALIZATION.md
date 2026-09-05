# Provider Integration and Operationalization

This slice turns the Guardian foundation into a provider-neutral operational boundary.

## Included

- canonical tier caps loaded from `config/zwca-dispatch.yaml`;
- Headroom, AST-aware and conservative compression adapters;
- provider-neutral `GuardedProviderGateway`;
- transactional budget reservations for parallel calls;
- Waste Ledger dashboard aggregations;
- tests for policy loading, compression and overspend prevention.

## Execution contract

1. The Guardian validates score, tier, context and budgets.
2. A budget reservation is created inside `BEGIN IMMEDIATE`.
3. Only then is the provider client called.
4. Completion cost and output tokens are audited.
5. The reservation is committed with actual cost or released on failure.

This ordering prevents concurrent calls from each observing the same available budget.

## Provider adapters

Concrete OpenAI, Azure OpenAI and Anthropic clients implement:

```python
class ProviderClient(Protocol):
    def complete(self, *, model: str, payload: str, **kwargs) -> ProviderResponse: ...
```

SDK-specific authentication, retry and pricing logic remains outside the Guardian. The gateway owns admission, reservations and audit.

## Compression adapters

- `HeadroomCompressor`: invokes the Headroom CLI and fails explicitly on invalid output.
- `ASTAwareCompressor`: pilot-safe structural reduction that removes comments and prioritizes declarations.
- `FallbackCompressor`: uses a secondary adapter when the primary compressor is unavailable.
- `ConservativeCompressor`: deterministic last-resort truncation, retained only as a fallback.

## Dashboard metrics

`dashboard/waste_ledger_metrics.py` exposes:

- artifacts observed;
- candidate, transmitted and rejected tokens;
- blended reduction;
- actual cost;
- admitted and blocked events;
- active reservations;
- breakdowns by tier and reason code.

## Pilot limitations

- PyYAML is required for policy loading.
- Headroom CLI argument compatibility must be validated against the installed version.
- The AST-aware adapter is language-neutral and not a replacement for tree-sitter platform adapters.
- Provider SDK adapters are represented by a stable protocol but concrete authenticated clients are deployment-specific.
- Reservation expiry currently occurs lazily during the next reservation attempt.
