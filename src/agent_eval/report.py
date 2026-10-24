"""Turn the raw runs into tables and one chart, and put the table in the README.

    python -m agent_eval.report

Writes results/summary.csv, results/by_category.csv, results/attack_success.png,
results/run_info.json, and replaces the text between the RESULTS markers in README.md.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "results"
ORDER = ["none", "warn", "spotlight", "guard"]


def pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def load(path: Path) -> pd.DataFrame:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    df = pd.DataFrame(rows)
    df["defense"] = pd.Categorical(df["defense"], ORDER, ordered=True)
    return df


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    att, ben = df[df.is_attack], df[~df.is_attack]
    rows = []
    for d in [d for d in ORDER if d in set(df.defense)]:
        a, b = att[att.defense == d], ben[ben.defense == d]
        per_seed = a.groupby("seed").attack_success.mean()
        rows.append({
            "defense": d,
            "attack_success_rate": a.attack_success.mean(),
            "asr_min_seed": per_seed.min(), "asr_max_seed": per_seed.max(),
            "attack_attempt_rate": a.attack_attempted.mean(),
            "task_done_under_attack": a.task_done.mean(),
            "task_done_no_attack": b.task_done.mean() if len(b) else float("nan"),
            "error_rate": df[df.defense == d].error.notna().mean(),
            # Validity check: a model that never calls a tool never sees an attack,
            # so a low attack rate only means something if this is high.
            "used_tools_rate": df[df.defense == d].tool_log.map(len).gt(0).mean(),
            "attack_runs": len(a), "benign_runs": len(b),
        })
    return pd.DataFrame(rows)


def markdown_table(s: pd.DataFrame, info: dict) -> str:
    lines = [
        f"Model: `{info['model']}` · runs: {info['total_runs']} · seeds: {info['seeds']} · date: {info['date']}",
        "",
        "| Defence | Attacks that worked | (lowest–highest seed) | Attacks tried | Task done, with attack | Task done, no attack | Used tools |",
        "|---|---|---|---|---|---|---|",
    ]
    for _, r in s.iterrows():
        lines.append(f"| {r.defense} | {pct(r.attack_success_rate)} | {pct(r.asr_min_seed)}–{pct(r.asr_max_seed)} | "
                     f"{pct(r.attack_attempt_rate)} | {pct(r.task_done_under_attack)} | {pct(r.task_done_no_attack)} | {pct(r.used_tools_rate)} |")
    lines += ["", "![Attack success by category and defence](results/attack_success.png)"]
    return "\n".join(lines)


def chart(by_cat: pd.DataFrame, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pivot = by_cat.pivot(index="category", columns="defense", values="attack_success_rate")
    pivot = pivot[[d for d in ORDER if d in pivot.columns]]
    ax = pivot.plot(kind="bar", figsize=(9, 4.5), width=0.8, color=["#9aa5b1", "#5b8def", "#2f6db5", "#1d3f6e"])
    ax.set_ylabel("Share of attacks that worked")
    ax.set_xlabel("")
    ax.set_ylim(0, 1)
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.legend(title="Defence", frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    plt.xticks(rotation=20, ha="right")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def main() -> None:
    df = load(RES / "raw" / "runs.jsonl")
    s = summarize(df)
    by_cat = (df[df.is_attack].groupby(["category", "defense"], observed=True)
              .agg(attack_success_rate=("attack_success", "mean"),
                   task_done=("task_done", "mean"), runs=("case_id", "size")).reset_index())
    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        commit = None
    info = {
        "model": ", ".join(sorted(df.model.unique())), "total_runs": len(df),
        "seeds": sorted(int(x) for x in df.seed.unique()), "date": dt.date.today().isoformat(),
        "attack_cases": int(df[df.is_attack].case_id.nunique()),
        "benign_cases": int(df[~df.is_attack].case_id.nunique()),
        "total_minutes": round(df.seconds.sum() / 60, 1), "git_commit": commit,
    }
    s.to_csv(RES / "summary.csv", index=False)
    by_cat.to_csv(RES / "by_category.csv", index=False)
    (RES / "run_info.json").write_text(json.dumps(info, indent=2))
    chart(by_cat, RES / "attack_success.png")

    table = markdown_table(s, info)
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    start, end = "<!-- RESULTS START -->", "<!-- RESULTS END -->"
    if start in text and end in text:
        text = text.split(start)[0] + start + "\n" + table + "\n" + end + text.split(end)[1]
        readme.write_text(text, encoding="utf-8")
    print(table)
    print("\nAttacks that worked, by category:\n")
    print(by_cat.pivot(index="category", columns="defense", values="attack_success_rate").map(pct).to_string())


if __name__ == "__main__":
    main()
