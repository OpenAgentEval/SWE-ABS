# 代码覆盖率（Coverage）获取机制

本文档详细说明了系统如何获取不同编程语言的测试代码覆盖率信息。

## 📋 目录

- [概述](#概述)
- [支持的语言](#支持的语言)
- [文件位置](#文件位置)
- [各语言详细说明](#各语言详细说明)
- [使用流程](#使用流程)
- [数据结构](#数据结构)

---

## 概述

系统通过 `utils/coverage_parse_utils.py` 提供了一个统一的覆盖率解析器，支持多种编程语言的覆盖率数据收集和分析。所有语言的覆盖率数据最终都被标准化为相同的数据结构，便于统一处理。

**核心文件：**
- `utils/coverage_parse_utils.py` - 覆盖率解析器
- `run_test/eval_model_test_patch.py` - 评估脚本（使用覆盖率）

---

## 支持的语言

| 语言 | 工具 | 文件格式 | 检测文件 |
|------|------|----------|----------|
| Python | coverage.py | JSON | `coverage.json` |
| Go | go test -coverprofile | Text | `coverage.out` |
| JavaScript | Istanbul/nyc | JSON | `coverage-final.json` |
| TypeScript | V8 coverage | JSON | `v8-coverage/*.json` |

---

## 文件位置

所有语言的覆盖率文件都存放在统一的目录结构中：

```
{instance_dir}/
└── workspace/
    └── coverage/
        ├── coverage.json          # Python
        ├── coverage.out           # Go
        ├── coverage-final.json    # JavaScript
        └── v8-coverage/           # TypeScript
            ├── coverage-1.json
            └── coverage-2.json
```

---

## 各语言详细说明

### 1. Python

**工具：** `coverage.py`

**文件：** `workspace/coverage/coverage.json`

**数据格式：**
```json
{
  "files": {
    "/app/lib/module.py": {
      "executed_lines": [1, 2, 3, 10, 15],
      "missing_lines": [4, 5, 6]
    },
    "/app/utils/helper.py": {
      "executed_lines": [1, 2, 3],
      "missing_lines": []
    }
  }
}
```

**解析函数：** `parse_python_coverage()` (L95-128)

**特点：**
- 直接提供行级别的执行/未执行信息
- 最简单直接的格式
- 路径自动标准化（移除 `/app` 前缀）

---

### 2. Go

**工具：** `go test -coverprofile`

**文件：** `workspace/coverage/coverage.out`

**数据格式：**
```
mode: set
github.com/org/repo/pkg/file.go:28.84,29.61 1 0
github.com/org/repo/pkg/file.go:29.61,31.3 1 1
github.com/org/repo/internal/utils.go:10.2,12.5 2 1
```

**格式说明：**
```
file:startLine.startCol,endLine.endCol numStatements count
```
- `startLine.startCol,endLine.endCol` - 代码块的起始和结束位置
- `numStatements` - 该代码块中的语句数
- `count` - 执行次数（> 0 表示已执行，== 0 表示未执行）

**解析函数：** `parse_go_coverage()` (L131-200)

**特点：**
- 使用正则表达式解析：`r'^(.+):(\d+)\.(\d+),(\d+)\.(\d+)\s+(\d+)\s+(\d+)$'`
- 将行范围展开为具体行号
- 自动处理模块路径（移除 `github.com/org/repo` 等前缀）
- 如果多个范围覆盖同一行，只要有一个 count > 0，该行就标记为已执行

**示例：**
```
github.com/example/repo/pkg/file.go:28.84,29.61 1 1
```
表示 file.go 的第 28-29 行被执行了 1 次。

---

### 3. JavaScript

**工具：** `Istanbul / nyc`

**文件：** `workspace/coverage/coverage-final.json`

**数据格式：**
```json
{
  "/app/src/file.js": {
    "path": "/app/src/file.js",
    "statementMap": {
      "0": {
        "start": {"line": 1, "column": 0},
        "end": {"line": 1, "column": 10}
      },
      "1": {
        "start": {"line": 5, "column": 2},
        "end": {"line": 7, "column": 3}
      }
    },
    "s": {
      "0": 1,    // 语句0执行了1次
      "1": 0     // 语句1未执行
    },
    "fnMap": {...},
    "f": {...},
    "branchMap": {...},
    "b": {...}
  }
}
```

**解析函数：** `parse_istanbul_coverage()` (L203-263)

**特点：**
- 使用 `statementMap` 定义语句位置
- 使用 `s` 对象记录语句执行次数
- 将语句范围的所有行标记为执行/未执行
- 自动去重：已执行的行会从未执行列表中移除

**解析逻辑：**
1. 遍历 `statementMap` 获取每个语句的行范围
2. 检查对应的执行计数 `s[stmt_id]`
3. 如果 count > 0，将范围内所有行标记为已执行
4. 否则标记为未执行

---

### 4. TypeScript

**工具：** `V8 coverage` (Node.js 内置)

**文件：** `workspace/coverage/v8-coverage/*.json` (多个文件)

**数据格式：**
```json
{
  "result": [
    {
      "scriptId": "123",
      "url": "file:///app/src/file.ts",
      "functions": [
        {
          "functionName": "foo",
          "ranges": [
            {
              "startOffset": 0,
              "endOffset": 100,
              "count": 1
            },
            {
              "startOffset": 50,
              "endOffset": 75,
              "count": 0
            }
          ],
          "isBlockCoverage": true
        }
      ]
    }
  ]
}
```

**解析函数：**
- `parse_v8_coverage()` (L266-366) - 简单估算方式
- `parse_v8_coverage_with_source()` (L369-480) - 精确映射方式

**特点：**
- V8 coverage 使用**字节偏移量**而非行号
- 需要转换字节偏移到行号

**两种转换方式：**

#### 方式1：简单估算（默认）
```python
AVG_LINE_LEN = 50  # 假设平均每行50字符
start_line = max(1, start_offset // AVG_LINE_LEN + 1)
end_line = max(start_line, end_offset // AVG_LINE_LEN + 1)
```
- 优点：快速，不需要读取源文件
- 缺点：不够精确

#### 方式2：精确映射（需要源文件）
```python
def get_line_offsets(filepath):
    """计算每行的起始字节偏移"""
    offsets = [0]
    with open(filepath, 'rb') as f:
        content = f.read()
    offset = 0
    for char in content:
        offset += 1
        if char == ord('\n'):
            offsets.append(offset)
    return offsets
```
- 优点：精确
- 缺点：需要访问源文件

**过滤规则：**
- 跳过 `node:` 内部模块
- 跳过 `node_modules` 目录
- 处理 `file://` URL 前缀

---

## 使用流程

### 1. 语言自动检测

系统会根据文件存在性自动检测语言类型：

```python
def detect_language_from_instance(instance_dir):
    coverage_dir = os.path.join(instance_dir, "workspace/coverage")

    if os.path.exists(os.path.join(coverage_dir, "coverage.json")):
        return "python"
    if os.path.exists(os.path.join(coverage_dir, "coverage.out")):
        return "go"
    if os.path.exists(os.path.join(coverage_dir, "coverage-final.json")):
        return "javascript"
    if os.path.exists(os.path.join(coverage_dir, "v8-coverage")):
        return "typescript"

    return None
```

### 2. 解析覆盖率

```python
# 单个instance
from utils.coverage_parse_utils import parse_coverage

coverage = parse_coverage("logs/instance_xxx")
# 返回 CoverageResult 对象

# 批量处理
from utils.coverage_parse_utils import compute_coverage_batch

coverage_results = compute_coverage_batch(
    log_dir,                   # 日志目录
    modified_related_lines     # 需要覆盖的行
)
# 返回 {instance_id: (coverage_rate, uncovered_lines)}
```

### 3. 计算覆盖率得分

```python
from utils.coverage_parse_utils import compute_coverage

coverage_rate, uncovered_lines = compute_coverage(
    instance_dir,
    modified_related_lines,
    use_key="exe_slice_lines_scope"
)

# coverage_rate: 0.0-1.0 之间的浮点数
# uncovered_lines: {file: [(line_num, line_content), ...]}
```

### 4. 在评估脚本中使用

在 `run_test/eval_model_test_patch.py` 中：

```python
# 1. 运行Docker时启用coverage
run_docker(
    ...
    use_coverage=args.use_coverage  # L207, 407, 557
)

# 2. 批量计算coverage
if args.use_coverage and args.must_cover_line:
    with open(args.must_cover_line) as f:
        modified_related_lines = json.load(f)

    coverage_results = compute_coverage_batch(
        str(log_dir),
        modified_related_lines
    )  # L850-851

# 3. 获取每个instance的结果
for instance_id, value in results_dict.items():
    if instance_id in coverage_results:
        coverage_rate, uncovered_lines = coverage_results[instance_id]
        # 保存到结果中
        all_predictions_test[instance_id]['meta']['coverage_rate'] = coverage_rate
        all_predictions_test[instance_id]['meta']['uncovered_lines'] = uncovered_lines
```

---

## 数据结构

### CoverageResult

所有语言的覆盖率数据最终都转换为这个统一的数据结构：

```python
@dataclass
class CoverageResult:
    language: str  # "python" | "go" | "javascript" | "typescript"
    files: Dict[str, FileCoverage]
```

### FileCoverage

单个文件的覆盖率信息：

```python
@dataclass
class FileCoverage:
    executed_lines: Set[int]   # 已执行的行号
    missing_lines: Set[int]    # 未执行的行号
```

### JSON输出格式

```json
{
  "language": "python",
  "files": {
    "lib/module.py": {
      "executed_lines": [1, 2, 3, 10, 15],
      "missing_lines": [4, 5, 6]
    },
    "utils/helper.py": {
      "executed_lines": [1, 2, 3],
      "missing_lines": []
    }
  }
}
```

### 覆盖率计算结果

```python
# compute_coverage() 返回值
(
    coverage_rate,      # float: 0.0-1.0, 或 404 表示无数据
    uncovered_lines     # Dict[str, List[Tuple[int, str]]]
)

# 示例
(
    0.857,  # 85.7% 覆盖率
    {
        "lib/module.py": [
            (4, "    def unused_function():"),
            (5, "        return None"),
            (6, "")
        ]
    }
)
```

---

## 命令行参数

在 `eval_model_test_patch.py` 中使用 coverage 相关参数：

```bash
python run_test/eval_model_test_patch.py \
  --input_path <predictions.json> \
  --use_coverage true \                    # 启用覆盖率收集
  --must_cover_line <modified_lines.json> \  # 必须覆盖的行
  --coverage_eval true \                   # 启用覆盖率评估模式
  --eval_gold_patch true \                 # 评估gold patch
  ...
```

**参数说明：**
- `--use_coverage`: 是否在运行测试时收集覆盖率数据
- `--must_cover_line`: 指定包含需要覆盖的行的JSON文件
- `--coverage_eval`: 启用覆盖率评估模式（只评估 0 < coverage < 1.0 的instance）

---

## 路径标准化

所有解析器都会进行路径标准化，移除常见的前缀：

- Python: 移除 `/app` 前缀
- Go: 移除 `github.com/org/repo` 等模块前缀
- JavaScript: 移除 `/app` 前缀
- TypeScript: 移除 `/app` 前缀和 `file://` URL前缀

**示例：**
```
/app/lib/module.py  →  lib/module.py
github.com/org/repo/pkg/file.go  →  pkg/file.go
file:///app/src/file.ts  →  src/file.ts
```

---

## 错误处理

### 返回值说明

- `coverage_rate = 1.0`: 完全覆盖
- `coverage_rate = 0.0-1.0`: 部分覆盖
- `coverage_rate = 404`: 无覆盖率数据（文件不存在或解析失败）

### 常见问题

1. **找不到覆盖率文件**
   - 检查 `workspace/coverage/` 目录是否存在
   - 确认测试运行时启用了覆盖率收集

2. **覆盖率为404**
   - 可能是语言检测失败
   - 可能是覆盖率文件格式不正确
   - 检查日志中的错误信息

3. **TypeScript覆盖率不准确**
   - V8 coverage 默认使用估算方式
   - 如需精确结果，使用 `parse_v8_coverage_with_source()` 并提供源文件路径

---

## 实现细节

### Python解析器 (L95-128)

```python
def parse_python_coverage(coverage_path, repo_prefix="/app"):
    result = CoverageResult(language="python")

    with open(coverage_path, 'r') as f:
        data = json.load(f)

    for file_path, file_info in data.get("files", {}).items():
        normalized_path = file_path.removeprefix(repo_prefix).lstrip("/")

        cov = FileCoverage()
        cov.executed_lines = set(file_info.get("executed_lines", []))
        cov.missing_lines = set(file_info.get("missing_lines", []))

        result.files[normalized_path] = cov

    return result
```

### Go解析器 (L131-200)

```python
def parse_go_coverage(coverage_path, module_prefix=""):
    result = CoverageResult(language="go")
    file_coverage = {}  # file -> {line: executed}

    with open(coverage_path, 'r') as f:
        for line in f:
            if line.startswith("mode:"):
                continue

            # 解析: file:start.col,end.col statements count
            match = re.match(r'^(.+):(\d+)\.(\d+),(\d+)\.(\d+)\s+(\d+)\s+(\d+)$', line)

            file_path = match.group(1)
            start_line = int(match.group(2))
            end_line = int(match.group(4))
            count = int(match.group(7))

            # 标记范围内的所有行
            for line_num in range(start_line, end_line + 1):
                if count > 0:
                    file_coverage[file_path][line_num] = True

    # 转换为FileCoverage对象
    for file_path, lines in file_coverage.items():
        cov = FileCoverage()
        for line_num, executed in lines.items():
            if executed:
                cov.executed_lines.add(line_num)
            else:
                cov.missing_lines.add(line_num)
        result.files[file_path] = cov

    return result
```

---

## 性能考虑

1. **批量处理**: 使用 `compute_coverage_batch()` 可以一次处理多个instance
2. **缓存**: 解析结果可以缓存避免重复解析
3. **TypeScript**: 简单估算模式比精确模式快得多
4. **内存**: 大型项目的覆盖率数据可能很大，注意内存使用

---

## 扩展新语言

如需支持新语言，需要：

1. 在 `detect_language_from_instance()` 中添加检测逻辑
2. 实现新的解析函数 `parse_xxx_coverage()`
3. 在 `parse_coverage()` 中添加调用
4. 确保返回标准的 `CoverageResult` 对象

**模板：**
```python
def parse_newlang_coverage(coverage_path, repo_prefix=""):
    result = CoverageResult(language="newlang")

    # 解析覆盖率文件
    # ...

    # 填充 result.files
    for file_path in ...:
        cov = FileCoverage()
        cov.executed_lines = set([...])
        cov.missing_lines = set([...])
        result.files[normalized_path] = cov

    return result
```

---

## 参考资料

- **coverage.py**: https://coverage.readthedocs.io/
- **Go coverage**: https://go.dev/blog/cover
- **Istanbul/nyc**: https://istanbul.js.org/
- **V8 coverage**: https://v8.dev/blog/javascript-code-coverage

---

**最后更新**: 2026-01-26
