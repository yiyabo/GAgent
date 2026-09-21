#!/usr/bin/env python3
"""Generate the synthetic diabetes cohort fixture for the user-journey suite.

Deterministic (seed=42) so the journey checkpoints can assert exact ground
truth. Output: diabetes_cohort.csv next to this script.

Design: 120 patients, two arms (metformin / control), baseline + follow-up
HbA1c / eGFR / creatinine. Metformin arm gets a clear HbA1c benefit and a
slightly slower eGFR decline — enough signal for the agent to find, small
enough to require real computation (not eyeballed).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SEED = 42
N_PER_ARM = 60
OUT = __file__.rsplit("/", 1)[0] + "/diabetes_cohort.csv"


def main() -> None:
    rng = np.random.default_rng(SEED)
    n = 2 * N_PER_ARM
    arms = ["二甲双胍组"] * N_PER_ARM + ["对照组"] * N_PER_ARM
    rng.shuffle(arms)

    age = np.clip(rng.normal(58, 11, n), 30, 85).round(0).astype(int)
    sex = rng.choice(["男", "女"], size=n, p=[0.52, 0.48])
    bmi = np.clip(rng.normal(26.5, 4.0, n), 17, 40).round(1)
    follow_months = rng.integers(6, 25, n)

    hba1c_base = np.clip(rng.normal(8.2, 0.9, n), 6.0, 12.0)
    hba1c_drop = np.where(
        pd.Series(arms) == "二甲双胍组",
        rng.normal(1.1, 0.5, n),
        rng.normal(0.3, 0.4, n),
    )
    hba1c_fu = np.clip(hba1c_base - hba1c_drop, 5.2, 12.5)

    egfr_base = np.clip(rng.normal(88, 16, n), 35, 130)
    egfr_decline = np.where(
        pd.Series(arms) == "二甲双胍组",
        rng.normal(2.5, 3.0, n),
        rng.normal(4.0, 3.5, n),
    )
    egfr_fu = np.clip(egfr_base - egfr_decline, 20, 135)

    creat_base = np.clip(145 - egfr_base * 0.92 + rng.normal(0, 4, n), 40, 150)
    creat_fu = np.clip(145 - egfr_fu * 0.92 + rng.normal(0, 4, n), 40, 160)

    df = pd.DataFrame({
        "患者ID": [f"P{i + 1:03d}" for i in range(n)],
        "年龄": age,
        "性别": sex,
        "BMI": bmi,
        "分组": arms,
        "HbA1c_基线": hba1c_base.round(2),
        "HbA1c_随访": hba1c_fu.round(2),
        "eGFR_基线": egfr_base.round(1),
        "eGFR_随访": egfr_fu.round(1),
        "肌酐_基线": creat_base.round(1),
        "肌酐_随访": creat_fu.round(1),
        "随访月数": follow_months,
        "合并高血压": rng.choice([0, 1], size=n, p=[0.55, 0.45]),
        "合并冠心病": rng.choice([0, 1], size=n, p=[0.82, 0.18]),
        "吸烟史": rng.choice([0, 1], size=n, p=[0.72, 0.28]),
    })
    df.to_csv(OUT, index=False, encoding="utf-8-sig")

    # Ground truth for the journey checkpoints (rounded like the agent should report).
    met = df[df["分组"] == "二甲双胍组"]
    ctl = df[df["分组"] == "对照组"]
    print("rows:", len(df), "| per arm:", len(met), len(ctl))
    print("HbA1c drop  met/ctl:",
          round((met["HbA1c_基线"] - met["HbA1c_随访"]).mean(), 2),
          round((ctl["HbA1c_基线"] - ctl["HbA1c_随访"]).mean(), 2))
    print("eGFR decline met/ctl:",
          round((met["eGFR_基线"] - met["eGFR_随访"]).mean(), 2),
          round((ctl["eGFR_基线"] - ctl["eGFR_随访"]).mean(), 2))
    print("eGFR_随访 mean met/ctl:", round(met["eGFR_随访"].mean(), 2), round(ctl["eGFR_随访"].mean(), 2))
    print("高血压患病率:", round(df["合并高血压"].mean(), 4), "| 吸烟率:", round(df["吸烟史"].mean(), 4))


if __name__ == "__main__":
    main()
