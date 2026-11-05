from __future__ import annotations

from team_soul.kernel import (
    ActivationPolicy,
    BoundaryPolicy,
    BindingTransferGuardReference,
    ClosurePolicy,
    CoeffectActivationReference,
    DeterministicClosureGateReference,
    DeterministicRetrievalReference,
    FUTURE_POLICY_SEAM_CATALOG,
    GenerativePolicy,
    POLICY_SEAM_CATALOG,
    ProblemLinkPolicy,
    ProjectionProblemLinkReference,
    RetrievalPolicy,
    ReasoningPolicy,
    TransferPolicy,
)
from team_soul.contracts import EvidenceKind, EvidenceRef, TransferCandidate


def test_five_learnable_seams_have_deterministic_reference_implementations() -> None:
    references = (
        (RetrievalPolicy, DeterministicRetrievalReference()),
        (TransferPolicy, BindingTransferGuardReference()),
        (ClosurePolicy, DeterministicClosureGateReference()),
        (ProblemLinkPolicy, ProjectionProblemLinkReference()),
        (ActivationPolicy, CoeffectActivationReference()),
    )
    assert len(POLICY_SEAM_CATALOG) == 5
    assert all(isinstance(reference, contract) for contract, reference in references)


def test_policy_seam_transfer_reference_keeps_external_veto_binding() -> None:
    candidate = TransferCandidate(
        source_case_id="source",
        target_case_id="target",
        shared_conditions=[],
        evidence_refs=[
            EvidenceRef(
                source_event_id="event:veto",
                evidence_kind=EvidenceKind.SUPPORT,
                note="high similarity",
            )
        ],
        external_veto=True,
    )

    result = BindingTransferGuardReference().evaluate(candidate)

    assert str(result.decision) == "REJECT"
    assert "EXTERNAL_VETO_BINDING" in result.reason_codes


def test_pre_v03_policy_seams_are_declared_but_not_bound() -> None:
    assert {item["name"] for item in FUTURE_POLICY_SEAM_CATALOG} == {
        "BoundaryPolicy",
        "ReasoningPolicy",
        "GenerativePolicy",
    }
    assert hasattr(BoundaryPolicy, "assess")
    assert hasattr(ReasoningPolicy, "trace")
    assert hasattr(GenerativePolicy, "propose")
