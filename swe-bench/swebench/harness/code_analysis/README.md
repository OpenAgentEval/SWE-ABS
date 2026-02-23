# 多语言代码分析模块

基于 **tree-sitter** 的多语言代码分析模块，用于支持 patch 覆盖率分析。

## 支持的语言

| 语言 | 文件扩展名 | 分析器类 |
|------|-----------|---------|
| Python | `.py`, `.pyw` | `PythonAnalyzer` |
| Go | `.go` | `GoAnalyzer` |
| JavaScript | `.js`, `.mjs`, `.cjs`, `.jsx` | `JavaScriptAnalyzer` |
| TypeScript | `.ts`, `.mts`, `.cts`, `.tsx` | `TypeScriptAnalyzer` |

## 快速开始

### 基本用法

```python
from swebench.harness.code_analysis import (
    analyze_source,
    detect_language_from_path,
    get_analyzer,
)

# 1. 从文件路径检测语言
language = detect_language_from_path("src/main.go")  # -> "go"

# 2. 分析源代码
source_code = open("src/main.go").read()
modified_lines = {10, 15, 20}  # patch 修改的行号

result = analyze_source(source_code, language, modified_lines)

# 3. 使用分析结果
print(result.executable_lines)   # 可执行的行号集合
print(result.modified_lines)     # 修正后的修改行（处理多行结构）
print(result.line_to_scope)      # 行号 -> (作用域类型, 作用域名称)
print(result.defs)               # 行号 -> 定义的变量集合
print(result.uses)               # 行号 -> 使用的变量集合
```

### 直接使用分析器

```python
from swebench.harness.code_analysis import PythonAnalyzer, GoAnalyzer

# 创建分析器实例
analyzer = GoAnalyzer()

# 获取可执行行
executable_lines, corrected_modified = analyzer.get_executable_lines(
    source_code,
    modified_lines.copy()
)

# 构建作用域映射
line_to_scope = analyzer.build_line_scope(source_code)

# 构建 def-use 关系
defs, uses = analyzer.build_def_use(source_code)
```

## 核心功能

### 1. 可执行行检测 (`get_executable_lines`)

识别代码中真正可执行的行，排除：
- 注释
- 空行
- 文档字符串（Python）
- 类型声明（TypeScript）

同时处理多行结构：
- 多行函数签名 -> 映射到函数定义行
- 多行函数调用 -> 映射到调用起始行

### 2. 作用域检测 (`build_line_scope`)

为每一行代码确定其所属的作用域：

| 作用域类型 | 说明 |
|-----------|------|
| `global` | 全局作用域 |
| `function` | 函数内部 |
| `class` | 类内部 |
| `method` | 方法内部（Go receiver 方法、JS/TS 类方法） |
| `interface` | 接口内部（TypeScript） |

### 3. Def-Use 分析 (`build_def_use`)

构建变量的定义-使用关系：
- **defs**: 每行定义了哪些变量
- **uses**: 每行使用了哪些变量

用于数据流分析和程序切片。

### 4. 全局语句过滤 (`filtered_global_modified`)

过滤掉无语义意义的全局语句修改：

| 语言 | 可忽略的全局语句 |
|------|-----------------|
| Python | `import`、简单赋值、文档字符串 |
| Go | `package`、`import`、`const`、`var`、`type` 声明 |
| JavaScript | `import`、`export`（re-export） |
| TypeScript | 上述 + `interface`、`type` 声明 |

## 语言特殊处理

### Go

Go 没有类的概念，使用 struct + method：

```go
// 方法会被识别为 method 作用域
// 作用域名称格式: "*ReceiverType.MethodName"
func (r *Router) HandleRequest(req Request) {
    // ...
}
```

### TypeScript

TypeScript 的类型声明是纯编译时构造，无运行时代码：

```typescript
// 以下都会被标记为可忽略的全局语句
interface User {
    name: string;
}

type ID = string | number;
```

## 扩展新语言

### 1. 创建分析器类

```python
# my_language_analyzer.py
from swebench.harness.code_analysis.base import BaseLanguageAnalyzer

class MyLanguageAnalyzer(BaseLanguageAnalyzer):
    def __init__(self):
        super().__init__("mylang")

    def _init_parser(self):
        import tree_sitter_mylang as ts_mylang
        from tree_sitter import Language, Parser
        self._tree_sitter_language = Language(ts_mylang.language())
        self._parser = Parser(self._tree_sitter_language)

    def get_executable_lines(self, src, modified_lines):
        # 实现...
        pass

    def build_line_scope(self, src):
        # 实现...
        pass

    def build_def_use(self, src):
        # 实现...
        pass

    def get_nodes_by_lineno(self, src):
        # 实现...
        pass

    def filtered_global_modified(self, line2scope, nodes_by_lineno, modified_lines):
        # 实现...
        pass
```

### 2. 注册分析器

```python
from swebench.harness.code_analysis import register_analyzer, register_extension
from my_language_analyzer import MyLanguageAnalyzer

# 注册语言
register_analyzer("mylang", MyLanguageAnalyzer)

# 注册文件扩展名
register_extension(".ml", "mylang")
register_extension(".myl", "mylang")
```

## API 参考

### 主要函数

| 函数 | 说明 |
|------|------|
| `analyze_source(src, language, modified_lines)` | 分析源代码，返回 `AnalysisResult` |
| `detect_language_from_path(file_path)` | 从文件路径检测语言 |
| `get_analyzer(language)` | 获取指定语言的分析器实例 |
| `is_language_supported(language)` | 检查语言是否支持 |
| `get_supported_languages()` | 获取所有支持的语言 |
| `get_supported_extensions()` | 获取所有支持的文件扩展名 |

### 注册函数

| 函数 | 说明 |
|------|------|
| `register_analyzer(language, analyzer_class)` | 注册新的语言分析器 |
| `register_extension(extension, language)` | 注册文件扩展名到语言的映射 |

### 数据类

#### `AnalysisResult`

```python
@dataclass
class AnalysisResult:
    executable_lines: Set[int]              # 可执行行
    modified_lines: Set[int]                # 修正后的修改行
    line_to_scope: Dict[int, Tuple[str, str]]  # 行 -> (作用域类型, 名称)
    defs: Dict[int, Set[str]]               # 行 -> 定义的变量
    uses: Dict[int, Set[str]]               # 行 -> 使用的变量
    nodes_by_lineno: Dict[int, Any]         # 行 -> tree-sitter 节点
```

#### `ScopeType`

```python
class ScopeType(Enum):
    GLOBAL = "global"
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    INTERFACE = "interface"
```

## 依赖

```
tree-sitter>=0.23.0
tree-sitter-python>=0.23.0
tree-sitter-go>=0.23.0
tree-sitter-javascript>=0.23.0
tree-sitter-typescript>=0.23.0
```

## 文件结构

```
code_analysis/
├── __init__.py              # 主 API 导出 + 语言注册表
├── base.py                  # 抽象基类定义
├── python_analyzer.py       # Python 分析器
├── go_analyzer.py           # Go 分析器
├── javascript_analyzer.py   # JavaScript 分析器
├── typescript_analyzer.py   # TypeScript 分析器
└── README.md                # 本文档
```
