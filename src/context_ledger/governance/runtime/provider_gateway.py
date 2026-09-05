from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from context_ledger.governance.runtime.guardian import CallEnvelope, EnforcementResult, Guardian
from context_ledger.governance.store.budget_reservations import BudgetReservations, Reservation


class ProviderClient(Protocol):
    def complete(self, *, model: str, payload: str, **kwargs: Any) -> "ProviderResponse": ...


@dataclass(frozen=True)
class ProviderResponse:
    content: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    raw: Any = None


class GuardedProviderGateway:
    """Provider-neutral execution boundary for OpenAI, Azure OpenAI and Anthropic adapters."""

    def __init__(
        self,
        guardian: Guardian,
        reservations: BudgetReservations,
        client: ProviderClient,
    ) -> None:
        self.guardian = guardian
        self.reservations = reservations
        self.client = client

    def complete(self, envelope: CallEnvelope, **provider_kwargs: Any) -> ProviderResponse:
        enforcement: EnforcementResult = self.guardian.enforce(envelope)
        reservation: Reservation = self.reservations.reserve(
            session_id=envelope.session_id,
            artifact_id=envelope.artifact_id,
            estimated_cost_usd=envelope.estimated_cost_usd,
            session_budget_usd=envelope.session_budget_usd,
            artifact_budget_usd=envelope.artifact_budget_usd,
        )
        try:
            response = self.client.complete(
                model=enforcement.envelope.model,
                payload=enforcement.envelope.payload,
                **provider_kwargs,
            )
            self.guardian.record_completion(
                enforcement,
                actual_cost_usd=response.cost_usd,
                output_tokens=response.output_tokens,
                quality_status="pending",
            )
            self.reservations.commit(reservation.reservation_id, response.cost_usd)
            return response
        except Exception:
            self.reservations.release(reservation.reservation_id)
            raise
