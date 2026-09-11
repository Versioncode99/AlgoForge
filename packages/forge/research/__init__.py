from forge.research.allocation import (
    DEFAULT_WEIGHTS,
    AllocationAdvice,
    Bucket,
    FrontierSignal,
    ResearchAllocation,
    adapt,
    realised,
)
from forge.research.campaign import (
    Campaign,
    CampaignError,
    CampaignProgress,
    CampaignStore,
    StoppingCriteria,
)
from forge.research.cpcv import (
    CombinatorialPlan,
    CombinatorialSplit,
    PathDistribution,
    combinatorial_purged_plan,
    path_distribution,
)
from forge.research.followup import FollowUp, Observation, derive, derive_many
from forge.research.frontier import (
    FrontierError,
    FrontierItem,
    FrontierState,
    ResearchFrontier,
    SearchKind,
)
from forge.research.hypotheses import (
    EdgeKind,
    Hypothesis,
    HypothesisError,
    HypothesisGraph,
    HypothesisStatus,
)
from forge.research.information import CostEstimate, InformationValue, estimate_cost, value_of
from forge.research.journal import EventKind, ResearchJournal
from forge.research.ledger import HoldoutConsumption, ResearchLedger
from forge.research.literature import (
    Claim,
    LiteratureError,
    RetrievalReport,
    Source,
    SourceStore,
    retrieve,
    topics_for,
)
from forge.research.mechanism import (
    MECHANISM_ALPHA,
    MINIMUM_TRADES_FOR_CONTROL,
    MechanismTest,
    entry_timing_control,
)
from forge.research.models import (
    EvidenceTier,
    PartitionReceipt,
    ResearchPartitions,
    ResearchSplitReceipt,
)
from forge.research.novelty import (
    NoveltyVerdict,
    Similarity,
    Subject,
    containment,
    subjects_from_families,
    subjects_from_hypotheses,
    subjects_from_templates,
)
from forge.research.novelty import assess as assess_novelty
from forge.research.promotion import Candidate as PromotionCandidate
from forge.research.promotion import (
    Prerequisites,
    PromotionOutcome,
    PromotionQueue,
    PromotionState,
    outcome_from_verdict,
)
from forge.research.promotion import assess as assess_promotion
from forge.research.runtime import (
    Diagnosis,
    Outcome,
    RuntimeMonitor,
    RuntimeState,
    WorkerHeartbeat,
)
from forge.research.skips import (
    NoveltyLevel,
    Skip,
    SkipKind,
    SkipLedger,
    admits,
    level_from_score,
)
from forge.research.split import chronological_split, source_data_hash

# `forge.research.synthesis` is deliberately NOT re-exported here. It imports the
# strategy IR, and `forge.strategy.models` imports `forge.research.models`, so
# pulling it into this package's __init__ makes the two packages import each
# other at module scope — which works or explodes depending on which one the
# process happens to touch first. Import it as `forge.research.synthesis`.
from forge.research.validation import (
    ValidationEvidence,
    expand_grid,
    reconstruct_paths,
    run_validation,
)
from forge.research.walkforward import (
    WalkForwardFold,
    WalkForwardPlan,
    WalkForwardResult,
    walk_forward_efficiency,
    walk_forward_plan,
)

__all__ = [
    "DEFAULT_WEIGHTS",
    "MECHANISM_ALPHA",
    "MINIMUM_TRADES_FOR_CONTROL",
    "AllocationAdvice",
    "Bucket",
    "Campaign",
    "CampaignError",
    "CampaignProgress",
    "CampaignStore",
    "Claim",
    "CombinatorialPlan",
    "CombinatorialSplit",
    "CostEstimate",
    "Diagnosis",
    "EdgeKind",
    "EventKind",
    "EvidenceTier",
    "FollowUp",
    "FrontierError",
    "FrontierItem",
    "FrontierSignal",
    "FrontierState",
    "HoldoutConsumption",
    "Hypothesis",
    "HypothesisError",
    "HypothesisGraph",
    "HypothesisStatus",
    "InformationValue",
    "LiteratureError",
    "MechanismTest",
    "NoveltyLevel",
    "NoveltyVerdict",
    "Observation",
    "Outcome",
    "PartitionReceipt",
    "PathDistribution",
    "Prerequisites",
    "PromotionCandidate",
    "PromotionOutcome",
    "PromotionQueue",
    "PromotionState",
    "ResearchAllocation",
    "ResearchFrontier",
    "ResearchJournal",
    "ResearchLedger",
    "ResearchPartitions",
    "ResearchSplitReceipt",
    "RetrievalReport",
    "RuntimeMonitor",
    "RuntimeState",
    "SearchKind",
    "Similarity",
    "Skip",
    "SkipKind",
    "SkipLedger",
    "Source",
    "SourceStore",
    "StoppingCriteria",
    "Subject",
    "ValidationEvidence",
    "WalkForwardFold",
    "WalkForwardPlan",
    "WalkForwardResult",
    "WorkerHeartbeat",
    "adapt",
    "admits",
    "assess_novelty",
    "assess_promotion",
    "chronological_split",
    "combinatorial_purged_plan",
    "containment",
    "derive",
    "derive_many",
    "entry_timing_control",
    "estimate_cost",
    "expand_grid",
    "level_from_score",
    "outcome_from_verdict",
    "path_distribution",
    "realised",
    "reconstruct_paths",
    "retrieve",
    "run_validation",
    "source_data_hash",
    "subjects_from_families",
    "subjects_from_hypotheses",
    "subjects_from_templates",
    "topics_for",
    "value_of",
    "walk_forward_efficiency",
    "walk_forward_plan",
]
