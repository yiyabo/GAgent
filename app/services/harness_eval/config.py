"""Explicit, frozen finite evaluation configurations."""
from dataclasses import asdict, dataclass, field
import hashlib, json, random, math
from .fixtures import CASES, BASE_CASES, CORPUS_VERSION


@dataclass
class EvalSuiteConfig:
    skills_arm: str | None = None
    variants: dict[str,dict] = field(default_factory=dict)
    target_root: str | None = None
    feature_overrides: dict[str,str] = field(default_factory=dict)
    schema_version: int = 2
    profile: str = 'production-like'
    cases: list[str] = field(default_factory=lambda:list(BASE_CASES))
    entries: list[str] = field(default_factory=lambda:['chat-native','plan-native','plan-external'])
    repetitions: int = 1
    order_seed: int = 42
    trial_wall_seconds: float = 600
    suite_wall_seconds: float = 3600
    close_reserve_seconds: float = 10
    output_max_tokens: int = 4096
    native_max_iterations: int = 48
    provider_attempt_limit: int = 64
    external_max_session_turns: int = 120
    external_launch_limit: int = 6
    token_stop_threshold: int = 1_000_000
    per_trial_token_stop_threshold: int | None = None
    campaign_root: str | None = None
    campaign_trial_limit: int = 18
    campaign_wall_seconds: float = 7200
    campaign_token_stop_threshold: int = 1_000_000
    campaign_provider_attempt_limit: int = 200
    campaign_external_launch_limit: int = 12
    cleanup_grace_seconds: float = 10
    revision: str = 'unknown'
    corpus_version: str = CORPUS_VERSION

    def validate(self):
        if self.skills_arm not in {None,'none','recommended'}:raise ValueError('unknown skill arm')
        if self.profile not in {'diagnostic','production-like'}:raise ValueError('unknown profile')
        if not self.cases or any(c not in CASES for c in self.cases):raise ValueError('unknown case')
        if not self.entries or any(e not in {'chat-native','plan-native','plan-external'} for e in self.entries):raise ValueError('unknown entry')
        if 'correction_journey' in self.cases and self.entries!=['chat-native']:raise ValueError('multi-turn controller case requires chat-native entry')
        if not 1<=self.repetitions<=3:raise ValueError('repetitions must be 1..3')
        if not 10<=self.trial_wall_seconds<=3600 or not 10<=self.suite_wall_seconds<=21600:raise ValueError('invalid wall limit')
        if not 0<=self.close_reserve_seconds<self.trial_wall_seconds:raise ValueError('invalid reserve')
        if not 1<=self.output_max_tokens<=32768 or not 1<=self.native_max_iterations<=64:raise ValueError('invalid generation limit')
        if not 1<=self.provider_attempt_limit<=256 or not 10<=self.external_max_session_turns<=500:raise ValueError('invalid call limit')
        if not 0<=self.external_launch_limit<=36 or self.token_stop_threshold<1:raise ValueError('invalid suite limit')
        if self.per_trial_token_stop_threshold is not None and self.per_trial_token_stop_threshold<1:raise ValueError('invalid trial token threshold')
        if self.campaign_trial_limit<1 or self.campaign_wall_seconds<=0 or self.campaign_token_stop_threshold<1 or self.campaign_provider_attempt_limit<1 or self.campaign_external_launch_limit<0:raise ValueError('invalid campaign limit')
        if not 0<=self.cleanup_grace_seconds<=60:raise ValueError('invalid cleanup allowance')
        if self.campaign_root:
            from pathlib import Path
            if not Path(self.campaign_root).is_absolute():raise ValueError('campaign_root must be absolute')
        allowed={'AGENT_ARGUMENT_VALIDATION_ENABLED','AGENT_SCHEMA_DISCLOSURE_V2_ENABLED','AGENT_TOOL_RECEIPT_COMPACTION_ENABLED','AGENT_RUNTIME_V2_ENABLED','ARTIFACT_VERSIONING_ENABLED','SKILL_RECOMMENDATION_V2_ENABLED','SKILL_CONTEXT_PROGRESSIVE_ENABLED','CHAT_RUN_SYNTHESIS_RESERVE_SECONDS'}
        def validate_features(values):
            if not isinstance(values,dict) or set(values)-allowed:raise ValueError('unsupported feature override')
            for name,value in values.items():
                if not isinstance(value,str):raise ValueError('feature overrides must be explicit strings')
                if name=='CHAT_RUN_SYNTHESIS_RESERVE_SECONDS':
                    try:number=float(value)
                    except (TypeError,ValueError):raise ValueError('invalid synthesis reserve override')
                    if not math.isfinite(number) or not 0<=number<=3600:raise ValueError('invalid synthesis reserve override')
                elif value not in {'0','1'}:raise ValueError('boolean feature override must be 0 or 1')
        validate_features(self.feature_overrides)
        for variant in self.variants.values():
            if not isinstance(variant,dict) or set(variant)-{'target_root','revision','feature_overrides','skills_arm'}:raise ValueError('variant cannot override evaluation budgets')
            if variant.get('skills_arm') not in {None,'none','recommended'}:raise ValueError('unknown variant skill arm')
            validate_features(variant.get('feature_overrides',{}))
        return self

    def to_dict(self):return asdict(self)

    def fingerprint(self):return hashlib.sha256(json.dumps(self.to_dict(),sort_keys=True).encode()).hexdigest()

    def schedule(self):
        rows=[{'case':c,'entry':e,'repetition':r} for r in range(self.repetitions) for c in self.cases for e in self.entries]
        if self.variants:rows=[{**r,'variant':v} for r in rows for v in self.variants]
        random.Random(self.order_seed).shuffle(rows)
        return rows

    @classmethod
    def diagnostic(cls,**overrides):
        return cls(**{'profile':'diagnostic','trial_wall_seconds':100,'close_reserve_seconds':5,'output_max_tokens':1200,'native_max_iterations':4,'provider_attempt_limit':5,**overrides}).validate()
