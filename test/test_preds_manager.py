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
print("Testing preds_manager refactoring")
print("=" * 60)

# ========== Test 1: Direct import of util/preds_manager ==========
print("\n[Test 1] Direct import of util/preds_manager")
try:
    sys.path.insert(0, str(Path(__file__).parent.parent / "util"))
    from sweabs_utils.preds_manager import ResultManager

    # Test basic methods
    assert hasattr(ResultManager, 'load'), "Missing load method"
    assert hasattr(ResultManager, 'save'), "Missing save method"
    assert hasattr(ResultManager, 'update_instance'), "Missing update_instance method"
    assert hasattr(ResultManager, 'update_instance_nested'), "Missing update_instance_nested method"
    assert hasattr(ResultManager, 'get_instance'), "Missing get_instance method"
    assert hasattr(ResultManager, 'get_failed_test_gen'), "Missing get_failed_test_gen method"
    assert hasattr(ResultManager, 'get_gold_patch_failures'), "Missing get_gold_patch_failures method"
    assert hasattr(ResultManager, 'get_low_coverage_instances'), "Missing get_low_coverage_instances method"

    print("✅ util/preds_manager.py imported successfully")
    print(f"   - Contains methods: load, save, update_instance, etc.")

except Exception as e:
    print(f"❌ util/preds_manager.py import failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ========== Test 2: ResultManager basic functionality ==========
print("\n[Test 2] ResultManager basic functionality")
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
        assert preds_path.exists(), "preds.json file not created"

        with open(preds_path, 'r') as f:
            data = json.load(f)
        assert "test-instance-1" in data, "Instance not saved"
        assert data["test-instance-1"]["model_test_patch"] == "diff --git a/test.py b/test.py"

        print("✅ update_instance works correctly")

        # Test 2: Retrieve instance
        instance = manager.get_instance("test-instance-1")
        assert instance is not None, "Failed to get instance"
        assert instance["meta"]["coverage_rate"] == 0.95

        print("✅ get_instance works correctly")

        # Test 3: Check if instance exists
        assert manager.instance_exists("test-instance-1") == True
        assert manager.instance_exists("non-existent") == False

        print("✅ instance_exists works correctly")

except Exception as e:
    print(f"❌ ResultManager basic functionality test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ========== Test 3: Nested key update functionality ==========
print("\n[Test 3] Nested key update functionality")
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
        assert instance["meta"]["pass_gold_patch_status"] == "success", "Nested key update failed"
        assert instance["meta"]["coverage_rate"] == 0.85, "Nested key update failed"
        assert instance["stage"][-1]["evaluation_info"]["status"] == "completed", "Array index update failed"

        print("✅ update_instance_nested works correctly")
        print(f"   - Supports dot-separated nested keys: meta.pass_gold_patch_status")
        print(f"   - Supports negative array index: stage.-1.evaluation_info")

except Exception as e:
    print(f"❌ Nested key update functionality test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ========== Test 4: Query functionality ==========
print("\n[Test 4] Query functionality")
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
        assert "fail-test-gen-1" in failed_test_gen, "Failed test generation instance not detected"

        gold_failures = manager.get_gold_patch_failures()
        assert "fail-gold-patch-1" in gold_failures, "Gold patch failure instance not detected"
        assert "fail-test-gen-1" in gold_failures, "Instance with empty meta should count as gold patch failure"

        low_coverage = manager.get_low_coverage_instances()
        assert "low-coverage-1" in low_coverage, "Low coverage instance not detected"
        assert "success-full-coverage" not in low_coverage, "Perfect coverage instance should not appear in low coverage list"

        print("✅ get_failed_test_gen works correctly")
        print("✅ get_gold_patch_failures works correctly")
        print("✅ get_low_coverage_instances works correctly")

        # Test statistics functionality
        stats = manager.get_statistics()
        assert stats["total_instances"] == 4, "Total instance count is incorrect"
        assert stats["successful_instances"] == 2, "Successful instance count is incorrect"

        print("✅ get_statistics works correctly")
        print(f"   - Total instances: {stats['total_instances']}")
        print(f"   - Successful instances: {stats['successful_instances']}")

except Exception as e:
    print(f"❌ Query functionality test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ========== Summary ==========
print("\n" + "=" * 60)
print("🎉 All tests passed! preds_manager refactoring successful!")
print("=" * 60)
print("\nRefactoring benefits:")
print("  - Unified preds.json management interface")
print("  - Thread-safe file operations")
print("  - Supports nested key updates (meta.pass_gold_patch_status)")
print("  - Supports array indexing (stage.-1.evaluation_info)")
print("  - Convenient query methods (failed instances, low coverage, etc.)")
print("\nRefactored files:")
print("  - util/preds_manager.py: shared manager class")
print("  - mini-swe-agent: 6 scripts")
print("  - swe-bench: 2 scripts")
print("  - SWE-bench_Pro-os: 3 scripts")
print("\nNext steps:")
print("  - Run actual scripts for verification")
print("  - Commit code to git")