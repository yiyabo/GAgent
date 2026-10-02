"""Explicit, frozen finite evaluation configurations."""
from dataclasses import asdict, dataclass, field
import hashlib, json, random
from .fixtures import CASES, CORPUS_VERSION


@dataclass
class EvalSuiteConfig:
    schema_version: int = 2
    profile: str = 'production-like'
    cases: list[str] = field(default_factory=lambda:list(CASES))
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
    revision: str = 'unknown'
    corpus_version: str = CORPUS_VERSION

    def validate(self):
        if self.profile not in {'diagnostic','production-like'}:raise ValueError('unknown profile')
        if not self.cases or any(c not in CASES for c in self.cases):raise ValueError('unknown case')
        if not self.entries or any(e not in {'chat-native','plan-native','plan-external'} for e in self.entries):raise ValueError('unknown entry')
        if not 1<=self.repetitions<=3:raise ValueError('repetitions must be 1..3')
        if not 10<=self.trial_wall_seconds<=3600 or not 10<=self.suite_wall_seconds<=21600:raise ValueError('invalid wall limit')
        if not 0<=self.close_reserve_seconds<self.trial_wall_seconds:raise ValueError('invalid reserve')
        if not 1<=self.output_max_tokens<=32768 or not 1<=self.native_max_iterations<=64:raise ValueError('invalid generation limit')
        if not 1<=self.provider_attempt_limit<=256 or not 10<=self.external_max_session_turns<=500:raise ValueError('invalid call limit')
        if not 0<=self.external_launch_limit<=36 or self.token_stop_threshold<1:raise ValueError('invalid suite limit')
        return self

    def to_dict(self):return asdict(self)

    def fingerprint(self):return hashlib.sha256(json.dumps(self.to_dict(),sort_keys=True).encode()).hexdigest()

    def schedule(self):
        rows=[{'case':c,'entry':e,'repetition':r} for r in range(self.repetitions) for c in self.cases for e in self.entries]
        random.Random(self.order_seed).shuffle(rows)
        return rows

    @classmethod
    def diagnostic(cls,**overrides):
        return cls(**{'profile':'diagnostic','trial_wall_seconds':100,'close_reserve_seconds':5,'output_max_tokens':1200,'native_max_iterations':4,'provider_attempt_limit':5,**overrides}).validate()
