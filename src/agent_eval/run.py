"""Run every test case under every defence and save the raw results.

    python -m agent_eval.run                      # full run, settings from config.yaml
    python -m agent_eval.run --limit 3            # quick smoke test: first 3 attacks + 3 benign
    python -m agent_eval.run --defenses none guard

Results go to results/raw/runs.jsonl (one line per run). If the run is
interrupted, start it again: runs that are already saved are skipped.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import yaml

from .agent import OpenAIChat, run_agent
from .defenses import DEFENSES, email_rule
from .score import attack_outcome, task_done
from .tools import ToolBox, inject, load_sandbox

ROOT = Path(__file__).resolve().parents[2]


def load_cases(limit: int | None = None) -> list[dict]:
    attacks = yaml.safe_load((ROOT / "cases" / "attacks.yaml").read_text(encoding="utf-8"))
    benign = yaml.safe_load((ROOT / "cases" / "benign.yaml").read_text(encoding="utf-8"))
    if limit:
        attacks, benign = attacks[:limit], benign[:limit]
    return attacks + benign


def run_one(chat, case: dict, defense: str, seed: int, max_steps: int) -> dict:
    docs, notes = load_sandbox(ROOT / "sandbox")
    if case.get("inject_into"):
        docs[case["inject_into"]] = inject(docs[case["inject_into"]], case["injection"])
    box = ToolBox(docs, notes, email_allowed=email_rule(defense, case["user_task"]))
    run = run_agent(chat, case["user_task"], box, defense, max_steps=max_steps, seed=seed)

    is_attack = case["category"] != "benign"
    succeeded, attempted = attack_outcome(case["attacker_goal"], run) if is_attack else (False, False)
    return {
        "case_id": case["id"], "category": case["category"], "defense": defense, "seed": seed,
        "is_attack": is_attack, "attack_success": succeeded, "attack_attempted": attempted,
        "task_done": task_done(case["utility_check"], run),
        "error": run["error"], "steps": run["steps"], "seconds": run["seconds"],
        "final_answer": run["final_answer"], "tool_log": run["tool_log"], "messages": run["messages"],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--limit", type=int, default=None, help="only the first N attack and N benign cases")
    ap.add_argument("--defenses", nargs="+", choices=DEFENSES, default=None)
    ap.add_argument("--out", default=str(ROOT / "results" / "raw" / "runs.jsonl"))
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    chat = OpenAIChat(cfg["base_url"], cfg["model"], cfg.get("api_key_env"), cfg.get("temperature", 0.0))
    defenses = args.defenses or cfg.get("defenses", DEFENSES)
    seeds = cfg.get("seeds", [1, 2, 3])
    cases = load_cases(args.limit)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["case_id"], r["defense"], r["seed"]))

    jobs = [(c, d, s) for s in seeds for d in defenses for c in cases if (c["id"], d, s) not in done]
    print(f"{len(cases)} cases x {len(defenses)} defences x {len(seeds)} seeds; {len(jobs)} runs to do "
          f"({len(done)} already saved). Model: {cfg['model']}")
    t0 = time.time()
    with out.open("a", encoding="utf-8") as f:
        for i, (case, defense, seed) in enumerate(jobs, 1):
            r = run_one(chat, case, defense, seed, cfg.get("max_steps", 8))
            r["model"] = cfg["model"]
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            flag = "ATTACK WORKED" if r["attack_success"] else ("error: " + r["error"] if r["error"] else "ok")
            print(f"[{i}/{len(jobs)}] {case['id']:<11} {defense:<9} seed={seed}  {flag}")
    print(f"Done in {(time.time() - t0) / 60:.1f} min. Now run: python -m agent_eval.report")


if __name__ == "__main__":
    main()
