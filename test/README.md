# SWE-PLUS 测试目录

本目录包含 SWE-PLUS 项目的测试脚本。

## 📁 目录结构

```
test/
├── test_parser_utils.py      # parser_utils 重构测试
├── test_preds_manager.py      # preds_manager 重构测试
└── README.md                  # 本文件
```

## 🧪 运行测试

在项目根目录下运行：

```bash
cd /path/to/SWE-PLUS

# 运行所有测试
python test/test_parser_utils.py
python test/test_preds_manager.py
```

或者在 test 目录下运行：

```bash
cd /path/to/SWE-PLUS/test

python test_parser_utils.py
python test_preds_manager.py
```

## ✅ 测试内容

### test_parser_utils.py

测试 parser_utils 重构：
- ✅ util/parser_utils.py 可以正常导入
- ✅ mini-swe-agent 的 parser_utils 可以正常导入
- ✅ Pro-os 的 parser_util 可以正常导入
- ✅ 所有函数功能正常

### test_preds_manager.py

测试 preds_manager 重构：
- ✅ util/preds_manager.py 可以正常导入
- ✅ ResultManager 基本功能（load, save, update_instance）
- ✅ 嵌套键更新功能（meta.coverage_rate, stage.-1）
- ✅ 查询功能（失败实例、低覆盖率等）
- ✅ 统计功能（get_statistics）

## 📊 测试输出

成功运行时，你会看到类似的输出：

```
============================================================
测试 preds_manager 重构
============================================================

[测试 1] 直接导入 util/preds_manager
✅ util/preds_manager.py 导入成功

[测试 2] ResultManager 基本功能
✅ update_instance 功能正常
✅ get_instance 功能正常

...

============================================================
🎉 所有测试通过！preds_manager 重构成功！
============================================================
```

---

**维护者**：SWE-PLUS Team
**最后更新**：2026-02-14
