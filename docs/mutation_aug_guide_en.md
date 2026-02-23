# Mutation Augmentation Pipeline Guide

> A pipeline that combines Stage 1 test patches with Stage 2 mutation patches to automatically generate and validate augmented tests (aug tests).
> Corresponds to the **Mutation Augmentation** stage in the paper.

## Overview

Mutation Augmentation consists of three phases:

| Phase | Function | Success Condition |
|-------|----------|-------------------|
| Phase 1: Merge | Combine Stage 1 + Stage 2 data into `pred_mutation.json` | Script exit code 0 |
| Phase 2: Aug No-Equ | Generate and validate aug tests for non-equivalent mutations | Aug test causes gold patch to pass and mutation patch to fail |
| Phase 3: Aug Equ | Generate and validate aug tests for equivalent mutations | Same as above |

**Prerequisites**: Stage 1 and Stage 2 (Mutation Generation) must be complete.

### Core Mechanism

For each iteration within an Aug Phase:
1. Agent generates augmented tests (`swebench_aug_mutation.py`)
2. Evaluation verifies whether the aug test is effective (`run_evaluation_test_mutation_aug.py`)
3. Failed instances are automatically retried (up to `--max-aug-retries` times)

---

## Quick Start

```bash
conda activate cyswe
cd mini-swe-agent
bash run_stage_mutation_aug.sh
```

**Default config**: model `openai/gpt-5`, 2 aug workers, 8 eval workers, 2 iterations, max 3 retries.

Custom run:

```bash
python run_stage_mutation_aug.py \
    --stage1-preds result/model_gen_test/my_run/preds.json \
    --stage2-output result/model_gen_test/my_run/stage2_mutation \
    --output result/mutation_aug \
    --run-id my_aug_run \
    --model openai/gpt-5 \
    --required-mutations 2 \
    --max-aug-retries 3
```

---

## Parameters

### Required

| Parameter | Description |
|-----------|-------------|
| `--stage1-preds` | Path to Stage 1's `preds.json` |
| `--stage2-output` | Stage 2 (Mutation Generation) output directory (containing `set1/`, `set2/` subdirs) |
| `--output` | Stage 3 output root directory |
| `--run-id` | Run ID |
| `--model` | Model for aug test generation |

### Optional

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--required-mutations` | 2 | Number of iterations N (should match Stage 2) |
| `--max-aug-retries` | 3 | Max retries per iteration |
| `--aug-workers` | 2 | Aug generation concurrency |
| `--eval-workers` | 8 | Evaluation concurrency |
| `--benchmark` | `swebench` | Benchmark set |
| `--subset` | `verified` | Data subset |
| `--split` | `test` | Data split |
| `--start-from-phase` | None | Phase name to resume from |
| `--instance-ids` | None | Comma-separated instance IDs |
| `--run-instance-file` | None | File containing instance IDs |

---

## Checkpoint Resume

| Phase Name | Phases Executed |
|------------|-----------------|
| `merge` | Phase 1 → 3 (all) |
| `aug_no_equ` | Phase 2 → 3 |
| `aug_equ` | Phase 3 only |

```bash
# Skip Merge, resume from Aug No-Equ
python run_stage_mutation_aug.py \
    --stage1-preds result/model_gen_test/my_run/preds.json \
    --stage2-output result/model_gen_test/my_run/stage2_mutation \
    --output result/mutation_aug \
    --run-id my_aug_run \
    --model openai/gpt-5 \
    --start-from-phase aug_no_equ

# Run Aug Equ only
python run_stage_mutation_aug.py ... --start-from-phase aug_equ
```

---

## Advanced Usage

### Target Specific Instances

```bash
python run_stage_mutation_aug.py \
    --stage1-preds result/model_gen_test/my_run/preds.json \
    --stage2-output result/model_gen_test/my_run/stage2_mutation \
    --output result/mutation_aug \
    --run-id my_aug_run \
    --model openai/gpt-5 \
    --instance-ids "django__django-11740,django__django-15280"
```

### Small-Scale Test (Recommended for First Run)

```bash
python run_stage_mutation_aug.py \
    --stage1-preds result/model_gen_test/my_run/preds.json \
    --stage2-output result/model_gen_test/my_run/stage2_mutation \
    --output result/mutation_aug \
    --run-id test_aug \
    --model openai/gpt-5 \
    --instance-ids "django__django-11740,django__django-15280" \
    --max-aug-retries 2
```

---

## Output Structure

```
result/mutation_aug/{run-id}/
├── pred_mutation.json                          # Phase 1 Merge output
├── preds_no_equ_mutation_aug_0.json            # Aug gen iter=0 (non-equiv)
├── preds_no_equ_mutation_aug_0_eval.json       # Aug eval iter=0 (non-equiv)
├── preds_no_equ_mutation_aug_1.json            # Aug gen iter=1 (non-equiv)
├── preds_no_equ_mutation_aug_1_eval.json       # Aug eval iter=1 (non-equiv)
├── preds_equ_mutation_aug_0.json               # Aug gen iter=0 (equiv)
├── preds_equ_mutation_aug_0_eval.json          # Aug eval iter=0 (equiv)
├── stage3_aug_report.json                      # Final report
└── logs/
    ├── stage3_aug.log
    ├── merge_<ts>.log
    ├── aug_no_equ_gen_<ts>.log
    ├── aug_no_equ_eval_<ts>.log
    ├── aug_equ_gen_<ts>.log
    └── aug_equ_eval_<ts>.log
```

### File Chain

| Phase | Iteration | Input | Output |
|-------|-----------|-------|--------|
| Aug No-Equ | iter=0 | `pred_mutation.json` | `preds_no_equ_mutation_aug_0.json` → `_eval.json` |
| Aug No-Equ | iter=1 | `preds_no_equ_mutation_aug_0_eval.json` | `preds_no_equ_mutation_aug_1.json` → `_eval.json` |
| Aug Equ | iter=0 | `pred_mutation.json` | `preds_equ_mutation_aug_0.json` → `_eval.json` |

---

## Success Criteria and Retry Logic

An instance in `*_eval.json` is considered **needing retry** when either:
1. `model_test_patch` is empty (aug generation failed)
2. `mutation_aug_evaluation_info.mutation_info.run_success_no_equ` is non-empty (aug test failed to catch non-equivalent mutation)

Up to `--max-aug-retries` retries are performed per iteration. After the limit is exceeded, a warning is logged and the pipeline continues — **the pipeline is never blocked** by individual instance failures.

---

## Monitoring

```bash
# Follow main log
tail -f result/mutation_aug/{run-id}/logs/stage3_aug.log

# Follow aug generation log
tail -f result/mutation_aug/{run-id}/logs/aug_no_equ_gen_*.log

# Check process
ps aux | grep run_stage_mutation_aug.py

# Count successful aug tests
python3 -c "
import json
data = json.load(open('result/mutation_aug/{run-id}/preds_no_equ_mutation_aug_0_eval.json'))
success = sum(1 for v in data.values()
              if not v.get('mutation_aug_evaluation_info', {}).get('mutation_info', {}).get('run_success_no_equ'))
print(f'Aug success: {success}/{len(data)}')
"
```

---

## Relationship to Adjacent Pipelines

- **Input**: Stage 1's `preds.json` + Stage 2's `set{i}/preds.json` (auto-discovered)
- **Output**: `*_eval.json` series containing final aug test patches and validation results
- **Paper significance**: Augmented tests distinguish equivalent from non-equivalent mutations, improving the quality of the SWE-abs benchmark

---

**Version**: v1.0 | **Last Updated**: 2026-02-21
