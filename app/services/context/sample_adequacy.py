"""Sample-size / modeling adequacy audit for clinical tabular datasets.

Deterministic rules (not a formal power analysis). Used to gate multi-model
AUC showcases when N or events-per-variable are too low for defensible
clinical prediction modeling.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# --- thresholds (tunable product knobs) ---
RED_MAX_N = 49
YELLOW_MAX_N = 199
RED_MAX_EVENTS = 9
YELLOW_MAX_EVENTS = 29
RED_MAX_EPV = 5.0
YELLOW_MAX_EPV = 10.0
GREEN_MIN_N = 200
GREEN_MIN_EVENTS = 50
GREEN_MIN_EPV = 10.0
DEFAULT_TARGET_PREDICTORS = 5
EPV_TARGET_STANDARD = 10
EPV_TARGET_CONSERVATIVE = 20

_ID_NAME_RE = re.compile(
    r"(?:^id$|_id$|^id_|patient|subject|case|record|住院号|病案|患者|编号|序号)",
    re.IGNORECASE,
)
_OUTCOME_NAME_RE = re.compile(
    r"(?:outcome|label|target|y\b|event|class|status|死亡|存活|复发|阳性|阴性|"
    r"预后|转归|并发症|感染|插管|成功|失败|是否|有无|group|分组|终点)",
    re.IGNORECASE,
)
_SEX_NAME_RE = re.compile(r"(?:^sex$|^gender$|性别)", re.IGNORECASE)
_DATE_NAME_RE = re.compile(
    r"(?:date|time|日期|时间|年月日)",
    re.IGNORECASE,
)
_TEXT_DTYPES = {"object", "string", "str", "category"}


@dataclass
class SampleAdequacyAudit:
    tier: str  # red | yellow | green | unknown
    n_rows: int
    n_subjects: Optional[int]
    n_complete_estimate: Optional[int]
    n_columns: int
    n_features_candidate: int
    outcome_column: Optional[str]
    outcome_positive_label: Optional[str]
    n_positive: Optional[int]
    n_negative: Optional[int]
    event_rate: Optional[float]
    epv: Optional[float]
    recommended_events_standard: int
    recommended_events_conservative: int
    recommended_n_at_observed_rate: Optional[int]
    recommended_n_at_10pct: Optional[int]
    recommended_n_at_20pct: Optional[int]
    gate: str
    reasons: List[str] = field(default_factory=list)
    guidance_zh: List[str] = field(default_factory=list)
    guidance_en: List[str] = field(default_factory=list)
    disclaimer_zh: str = ""
    disclaimer_en: str = ""
    target_predictors: int = DEFAULT_TARGET_PREDICTORS

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_prompt_block(self, *, language: str = "zh") -> str:
        """Compact block for profile summary / system injection."""
        lines = [
            "=== SAMPLE ADEQUACY AUDIT (deterministic; empirical EPV rules, not formal power analysis) ===",
            f"tier: {self.tier}",
            (
                f"N_rows={self.n_rows}"
                + (f" | N_subjects≈{self.n_subjects}" if self.n_subjects is not None else "")
                + f" | columns={self.n_columns}"
                + f" | candidate_features≈{self.n_features_candidate}"
            ),
        ]
        if self.outcome_column:
            rate = f"{self.event_rate:.1%}" if self.event_rate is not None else "n/a"
            lines.append(
                f"outcome={self.outcome_column}"
                + (f" | positive={self.outcome_positive_label!r}" if self.outcome_positive_label else "")
                + (f" | n+={self.n_positive}" if self.n_positive is not None else "")
                + (f" | n-={self.n_negative}" if self.n_negative is not None else "")
                + f" | event_rate={rate}"
                + (f" | EPV≈{self.epv:.2f}" if self.epv is not None else " | EPV=n/a")
            )
        else:
            lines.append(
                "outcome=not identified (user did not name y; no clear binary endpoint column)"
            )
        lines.append(
            f"if modeling with ~{self.target_predictors} predictors: "
            f"aim ≥{self.recommended_events_standard} events (EPV≥{EPV_TARGET_STANDARD}), "
            f"prefer ≥{self.recommended_events_conservative} (EPV≥{EPV_TARGET_CONSERVATIVE})"
        )
        n_bits = []
        if self.recommended_n_at_observed_rate is not None:
            n_bits.append(f"~{self.recommended_n_at_observed_rate} total at observed rate")
        if self.recommended_n_at_10pct is not None:
            n_bits.append(f"~{self.recommended_n_at_10pct} if event rate 10%")
        if self.recommended_n_at_20pct is not None:
            n_bits.append(f"~{self.recommended_n_at_20pct} if event rate 20%")
        if n_bits:
            lines.append("suggested total N (for standard EPV target): " + "; ".join(n_bits))
        lines.append(f"gate: {self.gate}")
        for r in self.reasons[:6]:
            lines.append(f"- reason: {r}")
        tips = self.guidance_zh if language.startswith("zh") else self.guidance_en
        for t in tips[:6]:
            lines.append(f"- tip: {t}")
        disc = self.disclaimer_zh if language.startswith("zh") else self.disclaimer_en
        if disc:
            lines.append(f"disclaimer: {disc}")
        lines.append("=== END SAMPLE ADEQUACY AUDIT ===")
        return "\n".join(lines)


def _is_id_like(name: str) -> bool:
    return bool(_ID_NAME_RE.search(str(name or "")))


def _is_date_like(name: str, dtype: str = "") -> bool:
    if _DATE_NAME_RE.search(str(name or "")):
        return True
    d = str(dtype or "").lower()
    return "datetime" in d or d.startswith("date")


def _is_sex_like(name: str) -> bool:
    return bool(_SEX_NAME_RE.search(str(name or "")))


def _candidate_feature_count(columns: Sequence[Dict[str, Any]]) -> int:
    """Rough p: non-ID, non-date columns (binary sex kept as feature)."""
    n = 0
    for col in columns:
        name = str(col.get("name") or "")
        dtype = str(col.get("dtype") or "")
        if not name:
            continue
        if _is_id_like(name):
            continue
        if _is_date_like(name, dtype):
            continue
        n += 1
    # subtract one if we will treat an outcome separately later (handled outside)
    return max(n, 0)


def _pick_subject_id_column(columns: Sequence[Dict[str, Any]], n_rows: int) -> Optional[str]:
    best: Optional[Tuple[float, str]] = None
    for col in columns:
        name = str(col.get("name") or "")
        if not _is_id_like(name):
            continue
        uniq = col.get("unique_count")
        try:
            u = int(uniq)
        except (TypeError, ValueError):
            continue
        if u <= 0 or n_rows <= 0:
            continue
        ratio = u / float(n_rows)
        # prefer near-unique IDs
        if ratio < 0.5:
            continue
        score = abs(1.0 - ratio)
        if best is None or score < best[0]:
            best = (score, name)
    return best[1] if best else None


def _score_outcome_column(col: Dict[str, Any]) -> float:
    name = str(col.get("name") or "")
    try:
        uniq = int(col.get("unique_count") or 0)
    except (TypeError, ValueError):
        uniq = 0
    if uniq != 2:
        return -1.0
    if _is_id_like(name) or _is_date_like(name, str(col.get("dtype") or "")):
        return -1.0
    if _is_sex_like(name):
        return 0.1  # weak; only if nothing else
    score = 1.0
    if _OUTCOME_NAME_RE.search(name):
        score += 3.0
    return score


def detect_outcome_column(
    columns: Sequence[Dict[str, Any]],
    *,
    preferred_name: Optional[str] = None,
) -> Optional[str]:
    if preferred_name:
        pref = preferred_name.strip().lower()
        for col in columns:
            name = str(col.get("name") or "")
            if name.strip().lower() == pref:
                return name
    ranked: List[Tuple[float, str]] = []
    for col in columns:
        s = _score_outcome_column(col)
        if s < 0:
            continue
        ranked.append((s, str(col["name"])))
    if not ranked:
        return None
    ranked.sort(key=lambda x: (-x[0], x[1]))
    # require at least weak non-sex signal, or accept sex only if sole binary
    top_score, top_name = ranked[0]
    if top_score < 1.0 and len(ranked) == 1:
        return None  # only sex-like binary — do not assume outcome
    if top_score < 1.0:
        return None
    return top_name


def _positive_from_value_counts(
    counts: Dict[Any, int],
) -> Tuple[Optional[Any], Optional[int], Optional[int]]:
    """Choose minority non-null class as event when binary; prefer 1/True/阳性."""
    if not counts:
        return None, None, None
    items = [(k, int(v)) for k, v in counts.items() if k is not None and str(k).strip() != ""]
    if len(items) != 2:
        return None, None, None
    (a, ca), (b, cb) = items[0], items[1]

    def _positive_preference(val: Any) -> int:
        s = str(val).strip().lower()
        if s in {"1", "1.0", "true", "yes", "y", "positive", "pos", "event", "case"}:
            return 100
        if any(x in s for x in ("阳性", "是", "有", "死亡", "复发", "发生", "成功", "失败")):
            # 失败/死亡 etc. often the event of interest
            if any(x in s for x in ("死亡", "复发", "发生", "失败", "阳性", "有")):
                return 90
            return 70
        if s in {"0", "0.0", "false", "no", "n", "negative", "neg", "control"}:
            return 0
        if any(x in s for x in ("阴性", "否", "无", "存活", "正常")):
            return 0
        return 50

    # Prefer explicit positive labels; else minority class
    scored = sorted(
        items,
        key=lambda kv: (-_positive_preference(kv[0]), kv[1], str(kv[0])),
    )
    if _positive_preference(scored[0][0]) >= 70:
        pos_label, n_pos = scored[0]
    else:
        # minority
        pos_label, n_pos = min(items, key=lambda kv: (kv[1], str(kv[0])))
    n_neg = sum(v for k, v in items if k != pos_label)
    return pos_label, n_pos, n_neg


def _load_binary_value_counts(file_path: str, column: str) -> Dict[Any, int]:
    import pandas as pd

    path = Path(file_path)
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            df = pd.read_csv(file_path, usecols=[column])
        elif suffix == ".tsv":
            df = pd.read_csv(file_path, sep="\t", usecols=[column])
        elif suffix in {".xlsx", ".xls", ".xlsm", ".ods"}:
            df = pd.read_excel(file_path, usecols=[column])
        elif suffix == ".parquet":
            df = pd.read_parquet(file_path, columns=[column])
        else:
            # fallback full read is avoided for unknown formats
            return {}
    except Exception:
        return {}
    series = df[column]
    vc = series.value_counts(dropna=True)
    return {k: int(v) for k, v in vc.items()}


def _load_nunique(file_path: str, column: str) -> Optional[int]:
    import pandas as pd

    path = Path(file_path)
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            df = pd.read_csv(file_path, usecols=[column])
        elif suffix == ".tsv":
            df = pd.read_csv(file_path, sep="\t", usecols=[column])
        elif suffix in {".xlsx", ".xls", ".xlsm", ".ods"}:
            df = pd.read_excel(file_path, usecols=[column])
        elif suffix == ".parquet":
            df = pd.read_parquet(file_path, columns=[column])
        else:
            return None
    except Exception:
        return None
    try:
        return int(df[column].nunique(dropna=True))
    except Exception:
        return None


def _ceil_div(num: float, den: float) -> Optional[int]:
    if den is None or den <= 0:
        return None
    return int(math.ceil(num / den))


def compute_sample_adequacy(
    *,
    n_rows: int,
    n_columns: int,
    columns: Sequence[Dict[str, Any]],
    file_path: Optional[str] = None,
    preferred_outcome: Optional[str] = None,
    target_predictors: int = DEFAULT_TARGET_PREDICTORS,
    language_hint: str = "zh",
) -> SampleAdequacyAudit:
    """Compute tiered sample adequacy from profile metadata (+ optional light IO)."""
    n_rows = int(n_rows or 0)
    n_columns = int(n_columns or 0)
    cols = [c for c in columns if isinstance(c, dict)]
    target_predictors = max(1, int(target_predictors or DEFAULT_TARGET_PREDICTORS))

    subject_col = _pick_subject_id_column(cols, n_rows)
    n_subjects: Optional[int] = None
    if subject_col and file_path:
        n_subjects = _load_nunique(file_path, subject_col)
    if n_subjects is None and subject_col:
        for c in cols:
            if str(c.get("name")) == subject_col:
                try:
                    n_subjects = int(c.get("unique_count"))
                except (TypeError, ValueError):
                    n_subjects = None
                break

    outcome_col = detect_outcome_column(cols, preferred_name=preferred_outcome)
    n_positive: Optional[int] = None
    n_negative: Optional[int] = None
    pos_label: Optional[str] = None
    event_rate: Optional[float] = None

    if outcome_col and file_path:
        counts = _load_binary_value_counts(file_path, outcome_col)
        pl, np_, nn_ = _positive_from_value_counts(counts)
        if pl is not None:
            pos_label = str(pl)
            n_positive = np_
            n_negative = nn_

    # feature count excludes outcome and IDs/dates
    feat_cols = []
    for c in cols:
        name = str(c.get("name") or "")
        if not name:
            continue
        if outcome_col and name == outcome_col:
            continue
        if _is_id_like(name) or _is_date_like(name, str(c.get("dtype") or "")):
            continue
        feat_cols.append(name)
    n_features = len(feat_cols) if feat_cols else max(0, n_columns - (1 if outcome_col else 0))
    # For EPV use min(declared features, target_predictors) as modeling p default?
    # Product rule: EPV = n+ / p_candidate (all candidate features) — harsh but honest.
    # Also report recommended events for target_predictors k.
    p_for_epv = max(n_features, 1) if n_features else max(n_columns - 1, 1)

    epv: Optional[float] = None
    if n_positive is not None and p_for_epv > 0:
        epv = float(n_positive) / float(p_for_epv)

    if n_positive is not None and (n_positive + (n_negative or 0)) > 0:
        event_rate = float(n_positive) / float(n_positive + (n_negative or 0))

    rec_ev_std = target_predictors * EPV_TARGET_STANDARD
    rec_ev_cons = target_predictors * EPV_TARGET_CONSERVATIVE
    rec_n_obs = _ceil_div(rec_ev_std, event_rate) if event_rate else None
    rec_n_10 = _ceil_div(rec_ev_std, 0.10)
    rec_n_20 = _ceil_div(rec_ev_std, 0.20)

    # complete-case rough: max null among feature cols if available
    n_complete: Optional[int] = None
    if cols and n_rows > 0:
        nulls = []
        for c in cols:
            name = str(c.get("name") or "")
            if name in feat_cols or (outcome_col and name == outcome_col):
                try:
                    nulls.append(int(c.get("null_count") or 0))
                except (TypeError, ValueError):
                    pass
        if nulls:
            # lower bound on complete rows for the worst single column
            n_complete = max(0, n_rows - max(nulls))

    effective_n = n_subjects if n_subjects is not None else n_rows
    reasons: List[str] = []
    tier = "unknown"

    # Tier rules (any red condition → red; else any yellow → yellow; else green if strong)
    red = False
    yellow = False
    if effective_n <= RED_MAX_N:
        red = True
        reasons.append(f"effective N={effective_n} < {RED_MAX_N + 1}")
    elif effective_n <= YELLOW_MAX_N:
        yellow = True
        reasons.append(f"effective N={effective_n} in [{RED_MAX_N + 1}, {YELLOW_MAX_N}]")

    if n_positive is not None:
        if n_positive <= RED_MAX_EVENTS:
            red = True
            reasons.append(f"events n+={n_positive} ≤ {RED_MAX_EVENTS}")
        elif n_positive <= YELLOW_MAX_EVENTS:
            yellow = True
            reasons.append(f"events n+={n_positive} ≤ {YELLOW_MAX_EVENTS}")
    else:
        # no outcome: use N/p ratio as soft signal
        if effective_n > 0 and p_for_epv > 0:
            ratio = effective_n / float(p_for_epv)
            if ratio < 5:
                red = True
                reasons.append(f"N/p≈{ratio:.1f} < 5 without identified outcome")
            elif ratio < 10:
                yellow = True
                reasons.append(f"N/p≈{ratio:.1f} < 10 without identified outcome")
            else:
                reasons.append("outcome not identified; tier from N and N/p only")

    if epv is not None:
        if epv < RED_MAX_EPV:
            red = True
            reasons.append(f"EPV≈{epv:.2f} < {RED_MAX_EPV}")
        elif epv < YELLOW_MAX_EPV:
            yellow = True
            reasons.append(f"EPV≈{epv:.2f} < {YELLOW_MAX_EPV}")

    if red:
        tier = "red"
    elif yellow:
        tier = "yellow"
    elif (
        effective_n >= GREEN_MIN_N
        and (n_positive is None or n_positive >= GREEN_MIN_EVENTS)
        and (epv is None or epv >= GREEN_MIN_EPV)
    ):
        tier = "green"
        reasons.append("meets green thresholds for limited supervised modeling")
    else:
        tier = "yellow"
        reasons.append("does not fully meet green thresholds; treat as caution")

    if tier == "red":
        gate = (
            "no multi-model AUC showcase; prefer descriptive stats / univariable / "
            "sample-size planning; prediction model not default recommendation"
        )
        disc_zh = (
            "当前样本量/结局事件数不足以支撑稳健的多变量或机器学习性能评估；"
            "AUC 等指标波动大、易过拟合，不宜作为临床决策或论文主要性能结论，仅可作探索。"
        )
        disc_en = (
            "Sample size / event count is insufficient for robust multivariable or ML "
            "performance claims; AUC is unstable and overfit-prone — exploratory only, "
            "not for clinical decision-making or primary manuscript claims."
        )
        guide_zh = [
            "优先：描述统计、组间比较、相关/单因素、缺失与偏倚说明",
            f"若目标纳入约 {target_predictors} 个自变量，建议至少约 {rec_ev_std} 个结局事件（稳妥 {rec_ev_cons}）",
            "研究方向不要默认「构建预测模型 / 多模型 AUC 对比」",
            "用户坚持建模时：最多极简逻辑回归（≤3 个先验变量）+ 明确探索性免责",
        ]
        guide_en = [
            "Prefer descriptive stats, group comparisons, univariable tests, bias notes",
            f"For ~{target_predictors} predictors aim ≥{rec_ev_std} events (prefer {rec_ev_cons})",
            "Do not default research directions to multi-model prediction + AUC tables",
            "If user insists: simple logistic regression (≤3 prespecified vars) + exploratory disclaimer only",
        ]
    elif tier == "yellow":
        gate = (
            "limited modeling only: simple model (e.g. LR ≤3 predictors), nested CV + CI; "
            "forbid multi-model beauty contest as main conclusion"
        )
        disc_zh = (
            "样本量/事件数偏紧，模型性能指标仅供探索；报告时需交叉验证与置信区间，"
            "避免将多模型 AUC 对比作为主要临床结论。"
        )
        disc_en = (
            "Sample/events are borderline; metrics are exploratory. Use CV and CIs; "
            "do not treat multi-model AUC comparison as a primary clinical conclusion."
        )
        guide_zh = [
            "可做极简预测探索（逻辑回归，变量宜少且有先验）",
            "必须报告交叉验证与不确定性；避免 RF/XGBoost 多模型选美当主结论",
            f"扩样本目标：约 {rec_ev_std}+ 事件（或总 N 见 suggested total N）后再认真建模",
        ]
        guide_en = [
            "Allow minimal predictive exploration (LR, few prespecified predictors)",
            "Require CV + uncertainty; avoid RF/XGB multi-model showcase as main result",
            f"Scale toward ≥{rec_ev_std} events before serious modeling claims",
        ]
    else:  # green
        gate = (
            "limited supervised modeling allowed; still require calibration notes and "
            "caution on external validity; multi-model OK only with proper validation"
        )
        disc_zh = (
            "样本量达到经验门槛，仍建议报告校准、验证策略与外推限制；"
            "单中心结果不宜直接当作临床部署性能。"
        )
        disc_en = (
            "Sample meets empirical gates; still report calibration, validation strategy, "
            "and external-validity limits. Single-center metrics are not deployment-ready."
        )
        guide_zh = [
            "允许有限监督学习，优先可解释模型与预注册变量",
            "报告区分度+校准；有条件做内部验证/时间切割",
        ]
        guide_en = [
            "Limited supervised modeling OK; prefer interpretable / prespecified models",
            "Report discrimination + calibration; internal validation when possible",
        ]

    return SampleAdequacyAudit(
        tier=tier,
        n_rows=n_rows,
        n_subjects=n_subjects,
        n_complete_estimate=n_complete,
        n_columns=n_columns,
        n_features_candidate=n_features,
        outcome_column=outcome_col,
        outcome_positive_label=pos_label,
        n_positive=n_positive,
        n_negative=n_negative,
        event_rate=event_rate,
        epv=epv,
        recommended_events_standard=rec_ev_std,
        recommended_events_conservative=rec_ev_cons,
        recommended_n_at_observed_rate=rec_n_obs,
        recommended_n_at_10pct=rec_n_10,
        recommended_n_at_20pct=rec_n_20,
        gate=gate,
        reasons=reasons,
        guidance_zh=guide_zh,
        guidance_en=guide_en,
        disclaimer_zh=disc_zh,
        disclaimer_en=disc_en,
        target_predictors=target_predictors,
    )


def audit_from_dataset_metadata(
    metadata: Dict[str, Any],
    *,
    file_path: Optional[str] = None,
    preferred_outcome: Optional[str] = None,
    target_predictors: int = DEFAULT_TARGET_PREDICTORS,
) -> SampleAdequacyAudit:
    columns = metadata.get("columns") or []
    # columns may be list of dicts from model_dump
    return compute_sample_adequacy(
        n_rows=int(metadata.get("total_rows") or 0),
        n_columns=int(metadata.get("total_columns") or 0),
        columns=columns,
        file_path=file_path or None,
        preferred_outcome=preferred_outcome,
        target_predictors=target_predictors,
    )


POLICY_TEXT_EN = """
- SAMPLE ADEQUACY / MODELING GATE POLICY (CRITICAL):
  * When tabular user data is profiled, READ the SAMPLE ADEQUACY AUDIT block (tier red/yellow/green).
  * RED: Do NOT recommend or run multi-model ML AUC showcases as the main path. Prefer descriptive stats,
    univariable tests, bias notes, and sample-size/event planning. Research directions must NOT default to
    "build a prediction model". If the user insists on modeling: at most a simple exploratory logistic
    regression with ≤3 prespecified predictors, with the audit disclaimer — never present AUC as clinically actionable.
  * YELLOW: Limited modeling only (simple model, nested CV, CIs). Forbid multi-model beauty contests as the conclusion.
  * GREEN: Limited supervised modeling OK; still require calibration/validation caveats.
  * Always restate N, events (if known), EPV, tier, and suggested N/events before proposing modeling.
  * Thresholds are empirical EPV/clinical rules of thumb, not formal power analysis — say so briefly.
""".strip()

POLICY_TEXT_ZH_HINT = (
    "样本量不足时禁止把多模型 AUC 当作主结论；红灯优先描述/单因素/扩样规划。"
)
