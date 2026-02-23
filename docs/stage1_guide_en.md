# Stage 1: Test Generation Pipeline Guide

> An end-to-end automated pipeline for generating high-coverage test patches for SWE-bench instances.

## Overview

Stage 1 consists of five phases:

| Phase | Function | Success Condition |
|-------|----------|-------------------|
| Phase 1: Test Generation | Agent generates test patches | `model_test_patch` non-empty and `exit_status` == "Submitted" |
| Phase 2: Hard Code Fix | Apply hard-coded corrections | Script exit code 0 |
| Phase 3: Gold Validation | Verify gold patch pass rate | `pass_gold_patch_status` == "success" |
| Phase 4: Coverage Fix | Agent improves test coverage | Script exit code 0 |
| Phase 5: Coverage Eval | Run tests and collect coverage data | `coverage_rate` == 1.0 |

Key features: automatic retry, checkpoint resume, multi-process file lock protection.

---

## Quick Start

```bash
conda activate cyswe
cd mini-swe-agent
bash run_stage1.sh
```

**Default config**: model `openai/gpt-5`, 2 workers, output to `result/model_gen_test/stage1_auto_debug`.

Custom run:

```bash
python run_stage1_auto.py \
    --output result/model_gen_test \
    --model openai/gpt-5 \
    --run-id my_experiment \
    --workers 4 \
    --benchmark swebench \
    --subset verified \
    --split test
```

---

## Parameters

### Required

| Parameter | Description |
|-----------|-------------|
| `--output` | Output root directory |
| `--model` | Model name (e.g. `openai/gpt-5`) |
| `--run-id` | Run ID (output subdirectory name) |

### Optional

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--workers` | 2 | Agent concurrency |
| `--benchmark` | `swebench` | Benchmark set (`swebench` or `swebenchpro`) |
| `--subset` | `verified` | Data subset |
| `--split` | `test` | Data split |
| `--repo-select-num` | 5 | Number of repos to randomly select |
| `--temperature` | 1.0 | Generation temperature |
| `--max-test-gen-retries` | 3 | Max retries for test generation |
| `--max-hard-code-fix-retries` | 3 | Max retries for hard code fix |
| `--max-combined-retries` | 2 | Max combined retry rounds |
| `--max-coverage-fix-attempts` | 2 | Max coverage fix attempts |
| `--eval-timeout` | 120 | Evaluation timeout (seconds) |
| `--max-eval-workers` | 12 | Evaluation concurrency |
| `--skip-coverage-fix` | False | Skip Phase 4 and 5 |
| `--start-from-phase` | None | Phase name to resume from |

---

## Checkpoint Resume

Use `--start-from-phase` to resume a pipeline that was interrupted:

| Phase Name | Phases Executed |
|------------|-----------------|
| `test_gen` | Phase 1 → 5 (all) |
| `hard_code_fix` | Phase 2 → 5 |
| `gold_eval` | Phase 3 → 5 |
| `coverage_fix` | Phase 4 → 5 |
| `coverage_eval` | Phase 5 only |

```bash
python run_stage1_auto.py \
    --start-from-phase gold_eval \
    --output result/model_gen_test \
    --model openai/gpt-5 \
    --run-id my_experiment \
    --workers 4
```

> ⚠️ `--output`, `--model`, and `--run-id` must be identical to the original run.

---

## Output Structure

```
result/model_gen_test/{run-id}/
├── preds.json                      # Primary data file
├── stage1_automation_report.json   # Final statistics report
├── logs/
│   ├── stage1_automation.log
│   ├── test_generation_<ts>.log
│   ├── hard_code_fix_<ts>.log
│   ├── gold_eval_<ts>.log
│   └── coverage_fix_<ts>.log
├── exit_statuses/
│   ├── test_gen_exit_statuses_<ts>.yaml
│   └── ...
└── traj/
    └── <instance_id>/
```

### Key Fields in preds.json

```json
{
  "instance_id": {
    "model_test_patch": "diff --git ...",
    "stage": [{"stage_name": "test_generation", "exit_status": "Submitted"}],
    "meta": {
      "pass_gold_patch_status": "success",
      "coverage_rate": 1.0
    }
  }
}
```

---

## Error Handling

**Script-level errors**: Non-zero exit code or corrupted JSON → pipeline stops immediately, manual intervention required.

**Instance-level errors**: Some instances fail → auto-retried within the retry limit, other instances unaffected.

**All instances fail** → treated as a script-level error, pipeline stops.

### Useful Debugging Commands

```bash
# Follow main log
tail -f result/model_gen_test/{run-id}/logs/stage1_automation.log

# Check running processes
ps aux | grep run_stage1_auto.py

# Validate preds.json format
python3 -m json.tool result/model_gen_test/{run-id}/preds.json

# Count successful instances
python3 -c "
import json
data = json.load(open('result/model_gen_test/{run-id}/preds.json'))
n = sum(1 for v in data.values() if v.get('meta', {}).get('pass_gold_patch_status') == 'success')
print(f'Success: {n}/{len(data)}')
"

# Remove stale lock file (only after confirming no process is running)
rm result/model_gen_test/{run-id}/.preds.json.lock
```

---

## Relationship to Downstream Pipelines

Stage 1's `preds.json` is the input for **Mutation Generation (Stage 2)**.
Ensure Stage 1 is complete and `meta.pass_gold_patch_status` is populated before running Stage 2.

---

**Version**: v2.0 | **Last Updated**: 2026-02-21
