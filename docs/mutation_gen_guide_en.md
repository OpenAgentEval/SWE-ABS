# Mutation Generation Pipeline Guide

> An automated pipeline that generates, tests, and judges mutation patches based on Stage 1 output.
> Corresponds to the **Mutation Generation** stage in the paper.

## Overview

Mutation Generation consists of three phases:

| Phase | Function | Success Condition |
|-------|----------|-------------------|
| Phase 1: Mutation Generation | Iteratively generate mutation patches | Each instance has ≥ N mutations |
| Phase 2: Initial Test Evaluation | Test whether mutations pass the initial test | Script exit code 0 |
| Phase 3: Mutation Judge | LLM judges mutation validity | Script exit code 0 |

**Prerequisite**: Stage 1 must be complete and produce a valid `preds.json`.

---

## Quick Start

```bash
conda activate cyswe
cd mini-swe-agent
bash run_stage_mutation_gen.sh
```

**Default config**: model `zai/glm-4.7`, 2 workers, 2 mutations required per instance, max 5 iterations.

Custom run:

```bash
python run_stage_mutation_gen.py \
    --output result/model_gen_test/stage1_auto_debug \
    --model zai/glm-4.7 \
    --run-id stage2_mutation \
    --workers 4 \
    --required-mutations 2 \
    --max-mutation-iterations 5
```

---

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--output` | required | Stage 1 output directory (containing `preds.json`) |
| `--model` | required | Model for mutation generation |
| `--run-id` | required | Run ID |
| `--required-mutations` | 2 | Number of mutations required per instance (N in the paper) |
| `--max-mutation-iterations` | 5 | Max iterations for Phase 1 |
| `--workers` | 2 | Mutation generation concurrency |
| `--max-eval-workers` | 8 | Evaluation concurrency |
| `--judge-models` | None | Comma-separated list of models for judging |
| `--judge-times` | 3 | Number of judge repeats per model |
| `--start-from-phase` | None | Phase name to resume from |
| `--instance-ids` | None | Comma-separated instance IDs to process |
| `--run-instance-file` | None | File containing instance IDs |

---

## Checkpoint Resume

| Phase Name | Phases Executed |
|------------|-----------------|
| `mutation_gen` | Phase 1 → 3 (all) |
| `init_test` | Phase 2 → 3 |
| `judge` | Phase 3 only |

```bash
# Resume from Phase 2
python run_stage_mutation_gen.py \
    --output result/model_gen_test/stage1_auto_debug \
    --model zai/glm-4.7 \
    --start-from-phase init_test

# Resume from Phase 3
python run_stage_mutation_gen.py \
    --output result/model_gen_test/stage1_auto_debug \
    --model zai/glm-4.7 \
    --start-from-phase judge
```

---

## Advanced Usage

### Target Specific Instances

```bash
# Specify IDs directly
python run_stage_mutation_gen.py \
    --output result/model_gen_test/stage1_auto_debug \
    --model zai/glm-4.7 \
    --instance-ids "django__django-11740,django__django-15280"

# Load from file
python run_stage_mutation_gen.py \
    --output result/model_gen_test/stage1_auto_debug \
    --model zai/glm-4.7 \
    --run-instance-file select_100_instances_ids.yaml
```

### Multi-Model Judge

```bash
python run_stage_mutation_gen.py \
    --output result/model_gen_test/stage1_auto_debug \
    --model zai/glm-4.7 \
    --judge-models "zai/glm-4.7,openai/gpt-5,deepseek/deepseek-chat"
```

---

## Output Structure

```
{output}/stage2_mutation/
├── preds.json                     # Primary data file (mutation fields added)
├── stage2_mutation_report.json    # Final report
├── logs/
│   ├── stage2_mutation.log
│   ├── mutation_generation_<ts>.log
│   ├── init_test_evaluation_<ts>.log
│   └── mutation_judge_<ts>.log
└── traj/
    └── mutation/
        └── <instance_id>/
```

### Key Fields Added to preds.json

- `all_mutatation_patch`: List of all generated mutation patches
- `evaluation_info.pass_init_test_status`: Phase 2 initial test result
- `judge_info`: Phase 3 LLM judge results (including `run_success_no_equ`, `run_fail_equ` categories)

---

## Monitoring

```bash
# Follow main log
tail -f result/model_gen_test/stage1_auto_debug/stage2_mutation/logs/stage2_mutation.log

# Follow latest mutation generation log
tail -f result/model_gen_test/stage1_auto_debug/stage2_mutation/logs/mutation_generation_*.log

# Check process
ps aux | grep run_stage_mutation_gen.py
```

---

## Success Criteria

- **Phase 1**: `len(all_mutatation_patch) >= --required-mutations` for each instance
- **Phase 2**: Script succeeds; individual mutation failures are tolerated
- **Phase 3**: Script succeeds; judge results are recorded in `judge_info`

---

## Relationship to Adjacent Pipelines

- **Input**: `preds.json` from Stage 1
- **Output**: Appends mutation fields to `preds.json`
- **Downstream**: Mutation Aug (Stage 3) uses the Phase 3 judge results as input

---

**Version**: v1.0 | **Last Updated**: 2026-02-21
