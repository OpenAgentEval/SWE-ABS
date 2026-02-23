# Stage 1：测试生成流水线使用指南

> 自动化端到端流水线，负责为 SWE-bench 实例生成高覆盖率的测试补丁。

## 功能概述

Stage 1 包含五个阶段：

| 阶段 | 功能 | 成功条件 |
|------|------|---------|
| Phase 1: Test Generation | 用 Agent 生成测试补丁 | `model_test_patch` 非空且 `exit_status` 为 "Submitted" |
| Phase 2: Hard Code Fix | 应用硬编码修正 | 脚本返回码为 0 |
| Phase 3: Gold Validation | 验证 gold patch 通过率 | `pass_gold_patch_status` == "success" |
| Phase 4: Coverage Fix | Agent 生成改进测试以提升覆盖率 | 脚本返回码为 0 |
| Phase 5: Coverage Eval | 执行测试并收集覆盖率数据 | `coverage_rate` == 1.0 |

核心特性：自动重试、断点续跑、多进程文件锁保护。

---

## 快速开始

```bash
conda activate cyswe
cd mini-swe-agent
bash run_stage1.sh
```

**默认配置**：模型 `openai/gpt-5`，2 workers，输出到 `result/model_gen_test/stage1_auto_debug`。

自定义运行：

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

## 主要参数

### 必需参数

| 参数 | 说明 |
|------|------|
| `--output` | 输出根目录 |
| `--model` | 使用的模型（如 `openai/gpt-5`） |
| `--run-id` | 运行 ID（输出子目录名） |

### 可选参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--workers` | 2 | Agent 并发数 |
| `--benchmark` | `swebench` | 基准集（`swebench` 或 `swebenchpro`） |
| `--subset` | `verified` | 数据子集 |
| `--split` | `test` | 数据分割 |
| `--repo-select-num` | 5 | 随机选取的 repo 数量 |
| `--temperature` | 1.0 | 生成温度 |
| `--max-test-gen-retries` | 3 | 测试生成最大重试次数 |
| `--max-hard-code-fix-retries` | 3 | Hard code fix 最大重试次数 |
| `--max-combined-retries` | 2 | 组合重试次数 |
| `--max-coverage-fix-attempts` | 2 | Coverage fix 最大尝试次数 |
| `--eval-timeout` | 120 | 评估超时（秒） |
| `--max-eval-workers` | 12 | 评估并发数 |
| `--skip-coverage-fix` | False | 跳过 Phase 4/5 |
| `--start-from-phase` | None | 断点续跑起始阶段 |

---

## 断点续跑

当流水线中途中断时，使用 `--start-from-phase` 从指定阶段继续：

| 阶段名 | 从该阶段开始执行 |
|--------|----------------|
| `test_gen` | Phase 1 → 5（全部） |
| `hard_code_fix` | Phase 2 → 5 |
| `gold_eval` | Phase 3 → 5 |
| `coverage_fix` | Phase 4 → 5 |
| `coverage_eval` | Phase 5 |

```bash
python run_stage1_auto.py \
    --start-from-phase gold_eval \
    --output result/model_gen_test \
    --model openai/gpt-5 \
    --run-id my_experiment \
    --workers 4
```

> ⚠️ 续跑时 `--output`、`--model`、`--run-id` 必须与首次运行完全一致。

---

## 输出文件结构

```
result/model_gen_test/{run-id}/
├── preds.json                      # 主数据文件
├── stage1_automation_report.json   # 最终统计报告
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

### preds.json 关键字段

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

## 错误处理

**脚本级错误**：脚本返回码非 0 或 JSON 损坏 → 立即停止流水线，需人工排查。

**实例级错误**：部分 instance 失败 → 在重试次数内自动重试，不影响其他 instance。

**所有 instance 均失败** → 视为脚本级错误，停止流水线。

### 常用排查命令

```bash
# 查看主日志
tail -f result/model_gen_test/{run-id}/logs/stage1_automation.log

# 检查进程
ps aux | grep run_stage1_auto.py

# 验证 preds.json 格式
python3 -m json.tool result/model_gen_test/{run-id}/preds.json

# 统计成功实例
python3 -c "
import json
data = json.load(open('result/model_gen_test/{run-id}/preds.json'))
n = sum(1 for v in data.values() if v.get('meta', {}).get('pass_gold_patch_status') == 'success')
print(f'Success: {n}/{len(data)}')
"

# 删除残留锁文件（确认无进程运行后）
rm result/model_gen_test/{run-id}/.preds.json.lock
```

---

## 与下游 Pipeline 的关系

Stage 1 的 `preds.json` 是 **Mutation Generation（Stage 2）** 的输入。
确保 Stage 1 完成后，`meta.pass_gold_patch_status` 字段有效，再运行 Stage 2。

---

**版本**：v2.0 | **最后更新**：2026-02-21
