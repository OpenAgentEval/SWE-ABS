# Mutation Generation（突变生成）流水线使用指南

> 基于 Stage 1 输出，自动生成、测试并判断 mutation patch 的流水线。
> 在论文中对应 **Mutation Generation** 阶段。

## 功能概述

Mutation Generation 包含三个阶段：

| 阶段 | 功能 | 成功条件 |
|------|------|---------|
| Phase 1: Mutation Generation | 迭代生成 mutation patch | 每个 instance 有 ≥ N 个 mutation |
| Phase 2: Initial Test Evaluation | 测试 mutation 是否通过初始测试 | 脚本返回码为 0 |
| Phase 3: Mutation Judge | LLM 判断 mutation 有效性 | 脚本返回码为 0 |

**前提条件**：已完成 Stage 1，输出目录中有有效的 `preds.json`。

---

## 快速开始

```bash
conda activate cyswe
cd mini-swe-agent
bash run_stage_mutation_gen.sh
```

**默认配置**：模型 `zai/glm-4.7`，2 workers，每个 instance 需要 2 个 mutation，最大迭代 5 次。

自定义运行：

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

## 主要参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--output` | 必需 | Stage 1 输出目录（含 `preds.json`） |
| `--model` | 必需 | Mutation 生成使用的模型 |
| `--run-id` | 必需 | 运行 ID |
| `--required-mutations` | 2 | 每个 instance 需要的 mutation 数量（对应论文中的 N） |
| `--max-mutation-iterations` | 5 | Phase 1 最大循环次数 |
| `--workers` | 2 | Mutation 生成并发数 |
| `--max-eval-workers` | 8 | 评估并发数 |
| `--judge-models` | None | Judge 使用的模型列表（逗号分隔） |
| `--judge-times` | 3 | 单模型 judge 重复次数 |
| `--start-from-phase` | None | 断点续跑起始阶段 |
| `--instance-ids` | None | 指定 instance ID（逗号分隔） |
| `--run-instance-file` | None | 从文件读取 instance ID |

---

## 断点续跑

| 阶段名 | 从该阶段开始执行 |
|--------|----------------|
| `mutation_gen` | Phase 1 → 3（全部） |
| `init_test` | Phase 2 → 3 |
| `judge` | Phase 3 |

```bash
# 从 Phase 2 继续
python run_stage_mutation_gen.py \
    --output result/model_gen_test/stage1_auto_debug \
    --model zai/glm-4.7 \
    --start-from-phase init_test

# 从 Phase 3 继续
python run_stage_mutation_gen.py \
    --output result/model_gen_test/stage1_auto_debug \
    --model zai/glm-4.7 \
    --start-from-phase judge
```

---

## 高级用法

### 指定特定 instance

```bash
# 直接指定 ID
python run_stage_mutation_gen.py \
    --output result/model_gen_test/stage1_auto_debug \
    --model zai/glm-4.7 \
    --instance-ids "django__django-11740,django__django-15280"

# 从文件读取
python run_stage_mutation_gen.py \
    --output result/model_gen_test/stage1_auto_debug \
    --model zai/glm-4.7 \
    --run-instance-file select_100_instances_ids.yaml
```

### 多模型 Judge

```bash
python run_stage_mutation_gen.py \
    --output result/model_gen_test/stage1_auto_debug \
    --model zai/glm-4.7 \
    --judge-models "zai/glm-4.7,openai/gpt-5,deepseek/deepseek-chat"
```

---

## 输出文件结构

```
{output}/stage2_mutation/          # 或自定义 run-id 目录
├── preds.json                     # 主数据文件（更新 mutation 字段）
├── stage2_mutation_report.json    # 最终报告
├── logs/
│   ├── stage2_mutation.log
│   ├── mutation_generation_<ts>.log
│   ├── init_test_evaluation_<ts>.log
│   └── mutation_judge_<ts>.log
└── traj/
    └── mutation/
        └── <instance_id>/
```

### preds.json 新增关键字段

- `all_mutatation_patch`：生成的所有 mutation patch 列表
- `evaluation_info.pass_init_test_status`：Phase 2 初始测试结果
- `judge_info`：Phase 3 LLM judge 结果（包含 `run_success_no_equ`、`run_fail_equ` 等分类）

---

## 查看运行状态

```bash
# 主日志
tail -f result/model_gen_test/stage1_auto_debug/stage2_mutation/logs/stage2_mutation.log

# 最新 mutation 生成日志
tail -f result/model_gen_test/stage1_auto_debug/stage2_mutation/logs/mutation_generation_*.log

# 检查进程
ps aux | grep run_stage_mutation_gen.py
```

---

## 成功标准

- **Phase 1**：每个 instance 的 `all_mutatation_patch` 长度 ≥ `--required-mutations`
- **Phase 2**：脚本成功执行，个别 mutation 失败不影响整体
- **Phase 3**：脚本成功执行，judge 结果记录在 `judge_info` 中

---

## 与相邻 Pipeline 的关系

- **输入**：来自 Stage 1 的 `preds.json`
- **输出**：在 `preds.json` 中追加 mutation 相关字段
- **下游**：Mutation Aug（Stage 3）以 Phase 3 的 judge 结果作为输入

---

**版本**：v1.0 | **最后更新**：2026-02-21
