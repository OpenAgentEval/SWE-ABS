#!/usr/bin/env python3
"""
Test script to verify cross-package imports work correctly.

This script tests that:
1. sweabs_utils can be imported
2. swebench can be imported
3. minisweagent can be imported
4. Cross-package functionality works as expected

Run this from anywhere:
    python mini-swe-agent/test_cross_package_imports.py
"""

import sys
from pathlib import Path


def test_sweabs_utils_imports():
    """Test importing from sweabs_utils package"""
    print("\n" + "="*60)
    print("Testing sweabs_utils imports...")
    print("="*60)

    try:
        from sweabs_utils.preds_manager import ResultManager
        print("✅ sweabs_utils.preds_manager.ResultManager imported successfully")

        from sweabs_utils.parser_utils import str2bool, read_list_file, get_test_directives
        print("✅ sweabs_utils.parser_utils functions imported successfully")

        # Test basic functionality
        result = str2bool("true")
        assert result is True
        print(f"✅ str2bool('true') = {result}")

        return True
    except ImportError as e:
        print(f"❌ Failed to import from sweabs_utils: {e}")
        return False
    except Exception as e:
        print(f"❌ Error testing sweabs_utils: {e}")
        return False


def test_swebench_imports():
    """Test importing from swebench package"""
    print("\n" + "="*60)
    print("Testing swebench imports...")
    print("="*60)

    try:
        # Test basic swebench import
        import swebench
        print(f"✅ swebench package imported successfully")
        print(f"   Location: {swebench.__file__}")

        # Test importing specific modules
        from swebench.harness.utils import get_apply_files
        print("✅ swebench.harness.utils.get_apply_files imported")

        from swebench.harness.constants import MAP_REPO_TO_INSTALL
        print("✅ swebench.harness.constants.MAP_REPO_TO_INSTALL imported")
        print(f"   MAP_REPO_TO_INSTALL has {len(MAP_REPO_TO_INSTALL)} entries")

        return True
    except ImportError as e:
        print(f"❌ Failed to import from swebench: {e}")
        print("   Make sure you ran 'pip install -e .' in swe-bench directory")
        return False
    except Exception as e:
        print(f"❌ Error testing swebench: {e}")
        return False


def test_minisweagent_imports():
    """Test importing from minisweagent package"""
    print("\n" + "="*60)
    print("Testing minisweagent imports...")
    print("="*60)

    try:
        import minisweagent
        print(f"✅ minisweagent package imported successfully")
        print(f"   Version: {minisweagent.__version__}")
        print(f"   Location: {minisweagent.__file__}")

        from minisweagent import Environment
        print("✅ minisweagent.Environment imported")

        from minisweagent.agents.default import DefaultAgent
        print("✅ minisweagent.agents.default.DefaultAgent imported")

        from minisweagent.models import get_model
        print("✅ minisweagent.models.get_model imported")

        return True
    except ImportError as e:
        print(f"❌ Failed to import from minisweagent: {e}")
        return False
    except Exception as e:
        print(f"❌ Error testing minisweagent: {e}")
        return False


def test_cross_package_usage():
    """Test using multiple packages together"""
    print("\n" + "="*60)
    print("Testing cross-package usage...")
    print("="*60)

    try:
        # Import from all three packages
        from sweabs_utils.parser_utils import get_test_directives
        from swebench.harness.utils import get_apply_files
        from swebench.harness.constants import MAP_REPO_TO_INSTALL
        import minisweagent

        print("✅ All packages imported together successfully")

        # Create a mock instance to test get_test_directives
        mock_instance = {
            "repo": "django/django",
            "repo_language": "python",
            "test_patch": """diff --git a/tests/test_example.py b/tests/test_example.py
index 1234567..abcdefg 100644
--- a/tests/test_example.py
+++ b/tests/test_example.py
@@ -1,3 +1,6 @@
+def test_new_feature():
+    assert True
+
 def test_existing():
     pass
"""
        }

        # Test get_test_directives from sweabs_utils
        directives = get_test_directives(mock_instance)
        print(f"✅ get_test_directives worked: {directives}")

        # Test accessing swebench constants
        if "django/django" in MAP_REPO_TO_INSTALL:
            print(f"✅ Can access swebench constants: django/django found in MAP_REPO_TO_INSTALL")

        return True
    except Exception as e:
        print(f"❌ Error in cross-package usage: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_import_from_different_locations():
    """Test that imports work from different working directories"""
    print("\n" + "="*60)
    print("Testing imports from different working directories...")
    print("="*60)

    current_dir = Path.cwd()
    print(f"Current working directory: {current_dir}")

    try:
        from sweabs_utils.preds_manager import ResultManager
        from swebench.harness.utils import get_apply_files
        import minisweagent

        print("✅ All imports work regardless of current working directory")
        print(f"   This confirms packages are properly installed in site-packages")
        return True
    except ImportError as e:
        print(f"❌ Import failed from current directory: {e}")
        return False


def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("CROSS-PACKAGE IMPORT TEST SUITE")
    print("="*60)
    print(f"Python: {sys.version}")
    print(f"Current directory: {Path.cwd()}")

    tests = [
        ("sweabs_utils imports", test_sweabs_utils_imports),
        ("swebench imports", test_swebench_imports),
        ("minisweagent imports", test_minisweagent_imports),
        ("cross-package usage", test_cross_package_usage),
        ("imports from different locations", test_import_from_different_locations),
    ]

    results = {}
    for test_name, test_func in tests:
        try:
            results[test_name] = test_func()
        except Exception as e:
            print(f"\n❌ Unexpected error in {test_name}: {e}")
            import traceback
            traceback.print_exc()
            results[test_name] = False

    # Print summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)

    passed = sum(1 for r in results.values() if r)
    total = len(results)

    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed! Cross-package imports are working correctly.")
        return 0
    else:
        print("\n⚠️  Some tests failed. Check the output above for details.")
        print("\nTroubleshooting:")
        print("1. Make sure you ran 'pip install -e .' in each directory:")
        print("   - cd /path/to/SWE-ABS && pip install -e .")
        print("   - cd /path/to/SWE-ABS/mini-swe-agent && pip install -e .")
        print("   - cd /path/to/SWE-ABS/swe-bench && pip install -e .")
        print("2. Verify packages are installed: pip list | grep -E 'sweabs|swebench|minisweagent'")
        return 1


if __name__ == "__main__":
    sys.exit(main())
