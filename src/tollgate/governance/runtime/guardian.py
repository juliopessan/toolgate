from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Protocol

from tollgate.governance.store.waste_ledger import LedgerEvent, WasteLedger


class GuardianBlocked(RuntimeError):
    """Raised when a provider call violates a mandatory Tollgate contract."""


class Compressor(Protocol):
    def __call__(self, payload: str, target_tokens: int) -> str: ...


@dataclass(frozen=True)
class TierPolicy:
    name: str
    input_token_cap: int
    output_token_cap: int
    max_recompress_attempts: int = 2


@dataclass(frozen=True)
class CallEnvelope:
    session_id: str
    project_id: str
    artifact_id: str
    payload: str
    candidate_tokens: int
    complexity_score: float | None
    tier: str | None
    provider: str
    model: str
    estimated_cost_usd: float
    artifact_budget_usd: float | None = None
    session_budget_usd: float | None = None
    recompress_attempt: int = 0


@dataclass(frozen=True)
class EnforcementResult:
    envelope: CallEnvelope
    admitted_tokens: int
    rejected_tokens: int
    decision: str


class Guardian:
    def __init__(
        self,
        ledger: WasteLedger,
        policies: dict[str, TierPolicy],
        token_counter: Callable[[str], int],
        compressor: Compressor | None = None,
    ) -> None:
        self.ledger = ledger
        self.policies = policies
        self.token_counter = token_counter
        self.compressor = compressor

    def enforce(self, envelope: CallEnvelope) -> EnforcementResult:
        self.ledger.ensure_session(
            envelope.session_id, envelope.project_id, envelope.session_budget_usd
        )
        self._require_score_and_tier(envelope)
        policy = self.policies[envelope.tier]
        self._enforce_cost_budgets(envelope)

        current = envelope
        current_tokens = self.token_counter(current.payload)
        while current_tokens > policy.input_token_cap:
            self._emit(
                current,
                gate="compression",
                decision="recompress",
                admitted=0,
                transmitted=0,
                rejected=max(current_tokens - policy.input_token_cap, 0),
                reason="TIER_INPUT_CAP_EXCEEDED",
            )
            if self.compressor is None or current.recompress_attempt >= policy.max_recompress_attempts:
                self._emit(
                    current,
                    gate="compression",
                    decision="blocked",
                    admitted=0,
                    transmitted=0,
                    rejected=current_tokens,
                    reason="RECOMPRESSION_EXHAUSTED",
                )
                raise GuardianBlocked(
                    f"payload has {current_tokens} tokens; tier {policy.name} cap is "
                    f"{policy.input_token_cap}"
                )
            compressed = self.compressor(current.payload, policy.input_token_cap)
            current = replace(
                current,
                payload=compressed,
                recompress_attempt=current.recompress_attempt + 1,
            )
            new_count = self.token_counter(compressed)
            if new_count >= current_tokens:
                self._emit(
                    current,
                    gate="compression",
                    decision="blocked",
                    admitted=0,
                    transmitted=0,
                    rejected=new_count,
                    reason="RECOMPRESSION_NO_PROGRESS",
                )
                raise GuardianBlocked("recompression did not reduce token count")
            current_tokens = new_count

        rejected = max(envelope.candidate_tokens - current_tokens, 0)
        self._emit(
            current,
            gate="admission",
            decision="admitted",
            admitted=current_tokens,
            transmitted=current_tokens,
            rejected=rejected,
            reason="WITHIN_TIER_AND_COST_BUDGET",
        )
        return EnforcementResult(current, current_tokens, rejected, "admitted")

    def record_completion(
        self,
        result: EnforcementResult,
        actual_cost_usd: float,
        output_tokens: int,
        quality_status: str,
    ) -> None:
        policy = self.policies[result.envelope.tier or ""]
        if output_tokens > policy.output_token_cap:
            self._emit(
                result.envelope,
                gate="audit",
                decision="violated",
                admitted=result.admitted_tokens,
                transmitted=result.admitted_tokens,
                rejected=output_tokens - policy.output_token_cap,
                reason="TIER_OUTPUT_CAP_EXCEEDED",
                actual_cost=actual_cost_usd,
                quality_status=quality_status,
            )
            raise GuardianBlocked("provider output exceeded the tier output cap")
        self._emit(
            result.envelope,
            gate="audit",
            decision="completed",
            admitted=result.admitted_tokens,
            transmitted=result.admitted_tokens,
            rejected=result.rejected_tokens,
            reason="CALL_COMPLETED",
            actual_cost=actual_cost_usd,
            quality_status=quality_status,
            evidence_basis="measured",
        )

    def _require_score_and_tier(self, envelope: CallEnvelope) -> None:
        if envelope.complexity_score is None:
            self._emit(envelope, "admission", "blocked", 0, 0, envelope.candidate_tokens, "MISSING_SCORE")
            raise GuardianBlocked("no score, no call")
        if envelope.tier is None or envelope.tier not in self.policies:
            self._emit(envelope, "admission", "blocked", 0, 0, envelope.candidate_tokens, "INVALID_TIER")
            raise GuardianBlocked("missing or unknown tier")

    def _enforce_cost_budgets(self, envelope: CallEnvelope) -> None:
        artifact_spend = self.ledger.artifact_spend(envelope.artifact_id)
        session_spend = self.ledger.session_spend(envelope.session_id)
        if envelope.artifact_budget_usd is not None and artifact_spend + envelope.estimated_cost_usd > envelope.artifact_budget_usd:
            self._emit(envelope, "admission", "blocked", 0, 0, envelope.candidate_tokens, "ARTIFACT_BUDGET_EXCEEDED")
            raise GuardianBlocked("artifact budget exceeded")
        if envelope.session_budget_usd is not None and session_spend + envelope.estimated_cost_usd > envelope.session_budget_usd:
            self._emit(envelope, "admission", "blocked", 0, 0, envelope.candidate_tokens, "SESSION_BUDGET_EXCEEDED")
            raise GuardianBlocked("session budget exceeded")

    def _emit(
        self,
        envelope: CallEnvelope,
        gate: str,
        decision: str,
        admitted: int,
        transmitted: int,
        rejected: int,
        reason: str,
        actual_cost: float | None = None,
        quality_status: str | None = None,
        evidence_basis: str = "estimated",
    ) -> None:
        self.ledger.append(
            LedgerEvent(
                session_id=envelope.session_id,
                artifact_id=envelope.artifact_id,
                event_type="provider_call",
                gate=gate,
                decision=decision,
                complexity_score=envelope.complexity_score,
                tier=envelope.tier,
                provider=envelope.provider,
                model=envelope.model,
                tokens_candidate=envelope.candidate_tokens,
                tokens_admitted=admitted,
                tokens_transmitted=transmitted,
                tokens_rejected=rejected,
                estimated_cost_usd=envelope.estimated_cost_usd,
                actual_cost_usd=actual_cost,
                artifact_budget_usd=envelope.artifact_budget_usd,
                session_budget_usd=envelope.session_budget_usd,
                recompress_attempt=envelope.recompress_attempt,
                quality_status=quality_status,
                evidence_basis=evidence_basis,
                reason_code=reason,
            )
        )
