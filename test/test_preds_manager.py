#!/usr/bin/env python3
"""
    Test whether the preds_manager refactoring was successful.

    Verifies:
    1. util/preds_manager.py can be imported successfully
    2. ResultManager basic functionality works correctly
    3. Nested key update functionality works correctly
    4. Query functionality works correctly
"""

import sys
import json
import tempfile
from pathlib import Path

print("=" * 60)
print("测试 preds_manager 重构")
print("=" * 60)

# ========== Test 1: Direct import of util/preds_manager ==========
print("\n[测试 1] 直接导入 util/preds_manager")
try:
    sys.path.insert(0, str(Path(__file__).parent.parent / "util"))
    from sweabs_utils.preds_manager import ResultManager

    # Test basic methods
    assert hasattr(ResultManager, 'load'), "缺少 load 方法"
    assert hasattr(ResultManager, 'save'), "缺少 save 方法"
    assert hasattr(ResultManager, 'update_instance'), "缺少 update_instance 方法"
    assert hasattr(ResultManager, 'update_instance_nested'), "缺少 update_instance_nested 方法"
    assert hasattr(ResultManager, 'get_instance'), "缺少 get_instance 方法"
    assert hasattr(ResultManager, 'get_failed_test_gen'), "缺少 get_failed_test_gen 方法"
    assert hasattr(ResultManager, 'get_gold_patch_failures'), "缺少 get_gold_patch_failures 方法"
    assert hasattr(ResultManager, 'get_low_coverage_instances'), "缺少 get_low_coverage_instances 方法"

    print("✅ util/preds_manager.py 导入成功")
    print(f"   - 包含方法: load, save, update_instance, etc.")

except Exception as e:
    print(f"❌ util/preds_manager.py 导入失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ========== Test 2: ResultManager basic functionality ==========
print("\n[测试 2] ResultManager 基本功能")
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        preds_path = Path(tmpdir) / "test_preds.json"

        # Create ResultManager
        manager = ResultManager(preds_path)

        # Test 1: Update instance
        manager.update_instance("test-instance-1", {
            "instance_id": "test-instance-1",
            "model_test_patch": "diff --git a/test.py b/test.py",
            "stage": [{
                "stage": "patch_generation",
                "status": "completed"
            }],
            "meta": {
                "pass_gold_patch_status": "success",
                "coverage_rate": 0.95
            }
        })

        # Verify data has been saved
        assert preds_path.exists(), "preds.json 文件未创建"

        with open(preds_path, 'r') as f:
            data = json.load(f)
        assert "test-instance-1" in data, "实例未保存"
        assert data["test-instance-1"]["model_test_patch"] == "diff --git a/test.py b/test.py"

        print("✅ update_instance 功能正常")

        # Test 2: Retrieve instance
        instance = manager.get_instance("test-instance-1")
        assert instance is not None, "无法获取实例"
        assert instance["meta"]["coverage_rate"] == 0.95

        print("✅ get_instance 功能正常")

        # Test 3: Check if instance exists
        assert manager.instance_exists("test-instance-1") == True
        assert manager.instance_exists("non-existent") == False

        print("✅ instance_exists 功能正常")

except Exception as e:
    print(f"❌ ResultManager 基本功能测试失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ========== Test 3: Nested key update functionality ==========
print("\n[测试 3] 嵌套键更新功能")
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        preds_path = Path(tmpdir) / "test_preds.json"
        manager = ResultManager(preds_path)

        # Create initial instance
        manager.update_instance("test-instance-2", {
            "instance_id": "test-instance-2",
            "model_test_patch": "",
            "stage": [{
                "stage": "patch_generation",
                "status": "incomplete"
            }],
            "meta": {
                "pass_gold_patch_status": "unknow",
                "coverage_rate": "unknow"
            }
        })

        # Update using nested keys
        manager.update_instance_nested("test-instance-2", {
            "meta.pass_gold_patch_status": "success",
            "meta.coverage_rate": 0.85,
            "stage.-1.evaluation_info": {
                "status": "completed",
                "outputs": "/path/to/outputs"
            }
        })

        # Verify the update
        instance = manager.get_instance("test-instance-2")
        assert instance["meta"]["pass_gold_patch_status"] == "success", "嵌套键更新失败"
        assert instance["meta"]["coverage_rate"] == 0.85, "嵌套键更新失败"
        assert instance["stage"][-1]["evaluation_info"]["status"] == "completed", "数组索引更新失败"

        print("✅ update_instance_nested 功能正常")
        print(f"   - 支持点号分隔的嵌套键: meta.pass_gold_patch_status")
        print(f"   - 支持数组负索引: stage.-1.evaluation_info")

except Exception as e:
    print(f"❌ 嵌套键更新功能测试失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ========== Test 4: Query functionality ==========
print("\n[测试 4] 查询功能")
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        preds_path = Path(tmpdir) / "test_preds.json"
        manager = ResultManager(preds_path)

        # Create multiple instances
        # Instance 1: test generation failed (model_test_patch is empty)
        manager.update_instance("fail-test-gen-1", {
            "instance_id": "fail-test-gen-1",
            "model_test_patch": "",
            "meta": {}
        })

        # Instance 2: test generation succeeded, but gold patch failed
        manager.update_instance("fail-gold-patch-1", {
            "instance_id": "fail-gold-patch-1",
            "model_test_patch": "diff --git a/test.py b/test.py",
            "meta": {
                "pass_gold_patch_status": "fail"
            }
        })

        # Instance 3: all passed, but coverage is low
        manager.update_instance("low-coverage-1", {
            "instance_id": "low-coverage-1",
            "model_test_patch": "diff --git a/test.py b/test.py",
            "meta": {
                "pass_gold_patch_status": "success",
                "coverage_rate": 0.6
            }
        })

        # Instance 4: all passed, coverage is perfect
        manager.update_instance("success-full-coverage", {
            "instance_id": "success-full-coverage",
            "model_test_patch": "diff --git a/test.py b/test.py",
            "meta": {
                "pass_gold_patch_status": "success",
                "coverage_rate": 1.0
            }
        })

        # Test query
        failed_test_gen = manager.get_failed_test_gen()
        assert "fail-test-gen-1" in failed_test_gen, "未检测到测试生成失败的实例"

        gold_failures = manager.get_gold_patch_failures()
        assert "fail-gold-patch-1" in gold_failures, "未检测到 gold patch 失败的实例"
        assert "fail-test-gen-1" in gold_failures, "空 meta 的实例应该算 gold patch 失败"

        low_coverage = manager.get_low_coverage_instances()
        assert "low-coverage-1" in low_coverage, "未检测到低覆盖率实例"
        assert "success-full-coverage" not in low_coverage, "完美覆盖率实例不应出现在低覆盖率列表"

        print("✅ get_failed_test_gen 功能正常")
        print("✅ get_gold_patch_failures 功能正常")
        print("✅ get_low_coverage_instances 功能正常")

        # Test statistics functionality
        stats = manager.get_statistics()
        assert stats["total_instances"] == 4, "总实例数统计错误"
        assert stats["successful_instances"] == 2, "成功实例数统计错误"

        print("✅ get_statistics 功能正常")
        print(f"   - 总实例数: {stats['total_instances']}")
        print(f"   - 成功实例数: {stats['successful_instances']}")

except Exception as e:
    print(f"❌ 查询功能测试失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ========== Summary ==========
print("\n" + "=" * 60)
print("🎉 所有测试通过！preds_manager 重构成功！")
print("=" * 60)
print("\n重构收益：")
print("  - 统一的 preds.json 管理接口")
print("  - 线程安全的文件操作")
print("  - 支持嵌套键更新（meta.pass_gold_patch_status）")
print("  - 支持数组索引（stage.-1.evaluation_info）")
print("  - 便捷的查询方法（失败实例、低覆盖率等）")
print("\n已重构的文件：")
print("  - util/preds_manager.py: 公共管理类")
print("  - mini-swe-agent: 6 个脚本")
print("  - swe-bench: 2 个脚本")
print("  - SWE-bench_Pro-os: 3 个脚本")
print("\n下一步：")
print("  - 运行实际的脚本验证")
print("  - 提交代码到 git")
