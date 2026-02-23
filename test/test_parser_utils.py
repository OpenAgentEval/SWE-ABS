#!/usr/bin/env python3
"""
    Test whether the parser_utils refactoring was successful.

    Verifies:
    1. util/parser_utils.py can be imported successfully
    2. mini-swe-agent's parser_utils can be imported successfully
    3. Pro-os's parser_util can be imported successfully
"""

import sys
from pathlib import Path

print("=" * 60)
print("测试 parser_utils 重构")
print("=" * 60)

# ========== Test 1: Direct import of util/parser_utils ==========
print("\n[测试 1] 直接导入 util/parser_utils")
try:
    sys.path.insert(0, str(Path(__file__).parent.parent / "util"))
    import parser_utils as util_parser

    # Test basic functions
    assert hasattr(util_parser, 'str2bool'), "缺少 str2bool"
    assert hasattr(util_parser, 'read_list_file'), "缺少 read_list_file"
    assert hasattr(util_parser, 'get_test_directives'), "缺少 get_test_directives"
    assert hasattr(util_parser, 'remove_conflicting_chunks'), "缺少 remove_conflicting_chunks"

    # Test constants
    assert hasattr(util_parser, 'LANGUAGE_TEST_EXTENSIONS'), "缺少 LANGUAGE_TEST_EXTENSIONS"
    assert hasattr(util_parser, 'FILTER_DIRS'), "缺少 FILTER_DIRS"

    print("✅ util/parser_utils.py 导入成功")
    print(f"   - 包含函数: str2bool, read_list_file, get_test_directives, etc.")
    print(f"   - 包含常量: LANGUAGE_TEST_EXTENSIONS, FILTER_DIRS, etc.")

except Exception as e:
    print(f"❌ util/parser_utils.py 导入失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ========== Test 2: Import parser_utils from mini-swe-agent ==========
print("\n[测试 2] 导入 mini-swe-agent/parser_utils")
try:
    sys.path.insert(0, str(Path(__file__).parent.parent / "mini-swe-agent" / "src"))
    from minisweagent.utils import parser_utils as mini_parser

    # Test functions imported from util
    assert hasattr(mini_parser, 'str2bool'), "缺少 str2bool"
    assert hasattr(mini_parser, 'get_test_directives'), "缺少 get_test_directives"

    # Test dedicated functions
    assert hasattr(mini_parser, 'is_strict_def_or_class'), "缺少 is_strict_def_or_class"
    assert hasattr(mini_parser, 'clean_full_diff'), "缺少 clean_full_diff"

    print("✅ mini-swe-agent/parser_utils 导入成功")
    print(f"   - 公共函数: str2bool, get_test_directives, etc.")
    print(f"   - 专用函数: is_strict_def_or_class, clean_full_diff, etc.")

except Exception as e:
    print(f"❌ mini-swe-agent/parser_utils 导入失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ========== Test 3: Import parser_util from Pro-os ==========
print("\n[测试 3] 导入 SWE-bench_Pro-os/parser_util")
try:
    sys.path.insert(0, str(Path(__file__).parent.parent / "SWE-bench_Pro-os"))
    from utils import parser_util as pro_parser

    # Test functions imported from util
    assert hasattr(pro_parser, 'str2bool'), "缺少 str2bool"
    assert hasattr(pro_parser, 'get_test_directives'), "缺少 get_test_directives"

    # Test dedicated functions
    assert hasattr(pro_parser, 'analyze_test_results'), "缺少 analyze_test_results"

    print("✅ Pro-os/parser_util 导入成功")
    print(f"   - 公共函数: str2bool, get_test_directives, etc.")
    print(f"   - 专用函数: analyze_test_results")

except Exception as e:
    print(f"❌ Pro-os/parser_util 导入失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ========== Test 4: Functional tests ==========
print("\n[测试 4] 功能测试")
try:
    # Test str2bool
    assert util_parser.str2bool("true") == True
    assert util_parser.str2bool("false") == False
    print("✅ str2bool 功能正常")

    # Test get_apply_files
    test_patch = """diff --git a/foo.py b/foo.py
diff --git a/bar.js b/bar.js"""
    files = util_parser.get_apply_files(test_patch)
    assert files == ["foo.py", "bar.js"], f"期望 ['foo.py', 'bar.js'], 实际 {files}"
    print("✅ get_apply_files 功能正常")

    # Test mini-swe-agent-specific functions
    line = "+def test_function():"
    result = mini_parser.is_strict_def_or_class(line)
    assert result == ("func", "test_function"), f"期望 ('func', 'test_function'), 实际 {result}"
    print("✅ is_strict_def_or_class 功能正常")

    # Test Pro-os-specific functions
    output = {
        "tests": [
            {"name": "test1", "status": "PASSED"},
            {"name": "test2", "status": "FAILED"},
        ]
    }
    failed, status_map = pro_parser.analyze_test_results(output)
    assert failed == ["test2"], f"期望 ['test2'], 实际 {failed}"
    assert status_map == {"test1": "PASSED", "test2": "FAILED"}
    print("✅ analyze_test_results 功能正常")

except Exception as e:
    print(f"❌ 功能测试失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ========== Summary ==========
print("\n" + "=" * 60)
print("🎉 所有测试通过！parser_utils 重构成功！")
print("=" * 60)
print("\n重构收益：")
print("  - 减少重复代码 ~250 行")
print("  - util/parser_utils.py: 公共函数统一维护")
print("  - mini-swe-agent: 保留 Python 专用函数")
print("  - Pro-os: 保留测试分析专用函数")
print("\n下一步：")
print("  - 运行实际的脚本验证（如 swebench_test.py）")
print("  - 提交代码到 git")
