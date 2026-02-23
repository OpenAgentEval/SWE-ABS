# Mutation Augmentation（增强测试生成）流水线使用指南

> 将 Stage 1 的测试补丁与 Stage 2 的 mutation patch 结合，自动生成并验证增强测试（aug test）的流水线。
> 在论文中对应 **Mutation Augmentation** 阶段。

## 功能概述

Mutation Augmentation 包含三个阶段：

| 阶段 | 功能 | 成功条件 |
|------|------|---------|
| Phase 1: Merge | 合并 Stage 1 + Stage 2 数据，生成 `pred_mutation.json` | 脚本返回码为 0 |
| Phase 2: Aug No-Equ | 为非等价 mutation 生成增强测试并验证 | aug test 能让 gold patch 通过、mutation patch 失败 |
| Phase 3: Aug Equ | 为等价 mutation 生成增强测试并验证 | 同上 |

**前提条件**：已完成 Stage 1 和 Stage 2（Mutation Generation）。

### 核心机制

每个 Aug Phase 对每次迭代执行以下循环：
1. 调用 Agent 生成增强测试（`swebench_aug_mutation.py`）
2. 运行评估验证增强测试是否有效（`run_evaluation_test_mutation_aug.py`）
3. 对仍然失败的 instance 自动重试（最多 `--max-aug-retries` 次）

---

## 快速开始

```bash
conda activate cyswe
cd mini-swe-agent
bash run_stage_mutation_aug.sh
```

**默认配置**：模型 `openai/gpt-5`，2 aug workers，8 eval workers，迭代 2 次，最大重试 3 次。

自定义运行：

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

## 主要参数

### 必需参数

| 参数 | 说明 |
|------|------|
| `--stage1-preds` | Stage 1 的 `preds.json` 路径 |
| `--stage2-output` | Stage 2（Mutation Generation）输出目录（含 `set1/`、`set2/` 子目录） |
| `--output` | Stage 3 输出根目录 |
| `--run-id` | 运行 ID |
| `--model` | Aug test 生成使用的模型 |

### 可选参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--required-mutations` | 2 | 迭代次数 N（与 Stage 2 保持一致） |
| `--max-aug-retries` | 3 | 每次迭代内的最大重试次数 |
| `--aug-workers` | 2 | Aug 生成并发数 |
| `--eval-workers` | 8 | 评估并发数 |
| `--benchmark` | `swebench` | 基准集 |
| `--subset` | `verified` | 数据子集 |
| `--split` | `test` | 数据分割 |
| `--start-from-phase` | None | 断点续跑起始阶段 |
| `--instance-ids` | None | 指定 instance ID（逗号分隔） |
| `--run-instance-file` | None | 从文件读取 instance ID |

---

## 断点续跑

| 阶段名 | 从该阶段开始执行 |
|--------|----------------|
| `merge` | Phase 1 → 3（全部） |
| `aug_no_equ` | Phase 2 → 3 |
| `aug_equ` | Phase 3 |

```bash
# 跳过 Merge，从 Aug No-Equ 继续
python run_stage_mutation_aug.py \
    --stage1-preds result/model_gen_test/my_run/preds.json \
    --stage2-output result/model_gen_test/my_run/stage2_mutation \
    --output result/mutation_aug \
    --run-id my_aug_run \
    --model openai/gpt-5 \
    --start-from-phase aug_no_equ

# 只运行 Aug Equ
python run_stage_mutation_aug.py ... --start-from-phase aug_equ
```

---

## 高级用法

### 指定特定 instance

```bash
python run_stage_mutation_aug.py \
    --stage1-preds result/model_gen_test/my_run/preds.json \
    --stage2-output result/model_gen_test/my_run/stage2_mutation \
    --output result/mutation_aug \
    --run-id my_aug_run \
    --model openai/gpt-5 \
    --instance-ids "django__django-11740,django__django-15280"
```

### 小规模测试（推荐首次运行）

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

## 输出文件结构

```
result/mutation_aug/{run-id}/
├── pred_mutation.json                          # Phase 1 Merge 输出
├── preds_no_equ_mutation_aug_0.json            # Aug gen iter=0（非等价）
├── preds_no_equ_mutation_aug_0_eval.json       # Aug eval iter=0（非等价）
├── preds_no_equ_mutation_aug_1.json            # Aug gen iter=1（非等价）
├── preds_no_equ_mutation_aug_1_eval.json       # Aug eval iter=1（非等价）
├── preds_equ_mutation_aug_0.json               # Aug gen iter=0（等价）
├── preds_equ_mutation_aug_0_eval.json          # Aug eval iter=0（等价）
├── stage3_aug_report.json                      # 最终报告
└── logs/
    ├── stage3_aug.log
    ├── merge_<ts>.log
    ├── aug_no_equ_gen_<ts>.log
    ├── aug_no_equ_eval_<ts>.log
    ├── aug_equ_gen_<ts>.log
    └── aug_equ_eval_<ts>.log
```

### 文件链路说明

| 阶段 | 迭代 | 输入 | 输出 |
|------|------|------|------|
| Aug No-Equ | iter=0 | `pred_mutation.json` | `preds_no_equ_mutation_aug_0.json` → `_eval.json` |
| Aug No-Equ | iter=1 | `preds_no_equ_mutation_aug_0_eval.json` | `preds_no_equ_mutation_aug_1.json` → `_eval.json` |
| Aug Equ | iter=0 | `pred_mutation.json` | `preds_equ_mutation_aug_0.json` → `_eval.json` |

---

## 成功标准与重试机制

一个 instance 在 `*_eval.json` 中被认为**需要重试**，当满足以下任一条件：
1. `model_test_patch` 为空（aug 生成失败）
2. `mutation_aug_evaluation_info.mutation_info.run_success_no_equ` 非空（aug test 未能抓住非等价 mutation）

每次迭代内最多重试 `--max-aug-retries` 次；超过次数后记录警告并继续，**不阻断流水线**。

---

## 查看运行状态

```bash
# 主日志
tail -f result/mutation_aug/{run-id}/logs/stage3_aug.log

# Aug 生成日志
tail -f result/mutation_aug/{run-id}/logs/aug_no_equ_gen_*.log

# 检查进程
ps aux | grep run_stage_mutation_aug.py

# 统计有效 aug test 数量
python3 -c "
import json
data = json.load(open('result/mutation_aug/{run-id}/preds_no_equ_mutation_aug_0_eval.json'))
success = sum(1 for v in data.values()
              if not v.get('mutation_aug_evaluation_info', {}).get('mutation_info', {}).get('run_success_no_equ'))
print(f'Aug success: {success}/{len(data)}')
"
```

---

## 与相邻 Pipeline 的关系

- **输入**：Stage 1 的 `preds.json` + Stage 2 的 `set{i}/preds.json`（自动发现）
- **输出**：`*_eval.json` 系列文件，包含最终的增强测试补丁和验证结果
- **论文意义**：增强测试用于区分等价/非等价 mutation，提升 SWE-abs 基准质量

---

**版本**：v1.0 | **最后更新**：2026-02-21
