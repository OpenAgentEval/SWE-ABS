"""
Analyze leaderboard ranking changes after SWE-abs evaluation.

Given eval results (final_results.json), computes each model's original resolve
rate (SWE-bench score) vs. SWE-abs resolve rate, and prints a table showing
the score drop and rank change for each model.

Usage:
    python tool_script/analyze_leaderboard.py \
        --results swe-bench/swe_plus_res/eval_agent/<run_id>/final_results.json \
        --key-map data/key_map_top30.json

The results JSON has this structure:
    {
      "<instance_id>": {
        "pass": ["<model_key>", ...],   # passed original AND augmented tests
        "fail": ["<model_key>", ...]    # passed original, failed augmented tests
      },
      ...
    }
"""

import argparse
import json
from collections import defaultdict

try:
    from scipy.stats import spearmanr
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


NAME_MAX = 42  # max chars for model name column


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def compute_ranks(model_patch_dict: dict, field: str) -> dict[str, int]:
    return {
        k: rank
        for rank, (k, _) in enumerate(
            sorted(model_patch_dict.items(), key=lambda x: len(x[1][field]), reverse=True),
            1,
        )
    }


def spearman(orig_rank: dict, abs_rank: dict) -> float | None:
    if not HAS_SCIPY:
        return None
    models = sorted(orig_rank.keys())
    rho, _ = spearmanr([orig_rank[m] for m in models], [abs_rank[m] for m in models])
    return rho


def analyze(
    dataset: dict,
    model_keys: list,
    key_map: dict,
    instance_ids: set | None = None,
    calculate_num: int | None = None,
):
    model_patch_dict = defaultdict(lambda: {"orig": [], "abs": []})
    total_use_num = 0
    kill_patch_num = 0
    strengthened_instances = 0

    for instance_id, value in dataset.items():
        if instance_ids is not None and instance_id not in instance_ids:
            continue

        fail_num = len(value["fail"])
        if fail_num > 0:
            kill_patch_num += fail_num
            strengthened_instances += 1

        for model_key in model_keys:
            if model_key not in key_map:
                continue
            if model_key in value["pass"]:
                model_patch_dict[model_key]["orig"].append(instance_id)
                model_patch_dict[model_key]["abs"].append(instance_id)
            elif model_key in value["fail"]:
                model_patch_dict[model_key]["orig"].append(instance_id)

        total_use_num += 1

    if calculate_num is None:
        calculate_num = total_use_num

    orig_rank = compute_ranks(model_patch_dict, "orig")
    abs_rank = compute_ranks(model_patch_dict, "abs")

    # Column widths
    W_NAME = NAME_MAX
    W_DATE = 10
    W_ORIG = 10
    W_ABS  = 11
    W_DROP = 10
    W_RANK = 10
    total_w = W_NAME + 1 + W_DATE + W_ORIG + W_ABS + W_DROP + W_RANK + 2

    header = (
        f"{'Model':<{W_NAME}} {'Date':<{W_DATE}}"
        f"{'Orig (%)':>{W_ORIG}}"
        f"{'SWE-abs (%)':>{W_ABS}}"
        f"{'Drop (%)':>{W_DROP}}"
        f"{'Rank':>{W_RANK}}"
    )
    print()
    print(header)
    print("-" * total_w)

    rank_changed = 0
    total_drop = 0.0
    rows = sorted(model_patch_dict.keys(), key=lambda k: orig_rank[k])

    for key in rows:
        value = model_patch_dict[key]
        orig_pct = len(value["orig"]) / calculate_num * 100
        abs_pct  = len(value["abs"])  / calculate_num * 100
        drop_pct = orig_pct - abs_pct

        model_name = truncate(key_map.get(key, key), W_NAME)
        date = key.split("_", 1)[0]

        rank_str = f"{orig_rank[key]:>2} → {abs_rank[key]:<2}"
        if orig_rank[key] != abs_rank[key]:
            rank_changed += 1
            rank_str += " *"

        print(
            f"{model_name:<{W_NAME}} {date:<{W_DATE}}"
            f"{orig_pct:>{W_ORIG - 1}.2f}%"
            f"{abs_pct:>{W_ABS - 1}.2f}%"
            f"{drop_pct:>{W_DROP - 1}.2f}%"
            f"  {rank_str}"
        )
        total_drop += drop_pct

    print("-" * total_w)

    avg_drop = total_drop / len(model_patch_dict) if model_patch_dict else 0
    rho = spearman(orig_rank, abs_rank)

    print(f"\nSummary:")
    print(f"  Models evaluated       : {len(model_patch_dict)}")
    print(f"  Instances evaluated    : {total_use_num}")
    print(f"  Strengthened instances : {strengthened_instances}")
    print(f"  Killed patches         : {kill_patch_num}")
    print(f"  Average score drop     : {avg_drop:.2f}%")
    print(f"  Models with rank change: {rank_changed}")
    if rho is not None:
        print(f"  Spearman ρ (orig→abs)  : {rho:.4f}")
    else:
        print(f"  Spearman ρ             : install scipy to enable")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze SWE-abs leaderboard ranking changes.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--results",
        required=True,
        help="Path to final_results.json from eval_agent_leaderboard.sh",
    )
    parser.add_argument(
        "--key-map",
        default="data/key_map_top30.json",
        help="Path to model key → display name mapping JSON",
    )
    parser.add_argument(
        "--instance-ids",
        default=None,
        help="Comma-separated instance IDs to restrict analysis to",
    )
    parser.add_argument(
        "--calculate-num",
        type=int,
        default=None,
        help="Total instance count for percentage calculation (defaults to instances in results)",
    )
    args = parser.parse_args()

    dataset = load_json(args.results)
    key_map = load_json(args.key_map)
    model_keys = list(key_map.keys())

    instance_ids = None
    if args.instance_ids:
        instance_ids = set(args.instance_ids.split(","))

    analyze(dataset, model_keys, key_map, instance_ids, args.calculate_num)


if __name__ == "__main__":
    main()
