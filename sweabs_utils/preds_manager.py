"""
SWE-PLUS Common preds.json Management Tool

Provides unified read/write management of preds.json across three repositories.

Data format: {instance_id: {...}} (dict format, not list format)

Version: v0.1.0
Last updated: 2026-02-14
Maintenance note: This file is shared across three repositories; consider compatibility when modifying.
"""

import json
import fcntl
import time
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from contextlib import contextmanager


class ResultManager:
    """
    Unified read/write management for preds.json.

    Data format: {instance_id: instance_data}

    Usage example:
        manager = ResultManager("result/model_gen_test/preds.json")

        # Update a single instance
        manager.update_instance("django-11141", {
            "model_test_patch": "diff --git ...",
            "meta": {"pass_gold_patch_status": "success"}
        })

        # Update using nested keys
        manager.update_instance_nested("django-11141", {
            "meta.pass_gold_patch_status": "success",
            "meta.coverage_rate": 0.95
        })

        # Get failed instances
        failed = manager.get_failed_test_gen()
    """

    def __init__(self, preds_path: Union[str, Path]):
        """
        Initialize ResultManager.

        Args:
            preds_path: path to preds.json
        """
        self.preds_path = Path(preds_path)
        self._lock_path = self.preds_path.parent / f".{self.preds_path.name}.lock"

        # Ensure parent directory exists
        self.preds_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _file_lock(self, timeout=30):
        """
        File lock context manager for multi-process safety.

        Args:
            timeout: seconds to wait before giving up on acquiring the lock

        Yields:
            lock file handle

        Raises:
            TimeoutError: if the lock cannot be acquired within timeout seconds
        """
        # Ensure lock file's parent directory exists
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)

        lock_file = open(self._lock_path, 'w')
        start_time = time.time()

        try:
            while True:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except IOError:
                    if time.time() - start_time > timeout:
                        raise TimeoutError(f"Could not acquire file lock on {self._lock_path} within {timeout} seconds")
                    time.sleep(0.1)

            yield lock_file
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except:
                pass
            lock_file.close()

    def load(self) -> Dict[str, Any]:
        """
        Load preds.json (no lock; for read-only use).

        Returns:
            Dict containing all prediction data: {instance_id: instance_data}
        """
        if not self.preds_path.exists():
            return {}

        try:
            with open(self.preds_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"Warning: Failed to parse {self.preds_path}: {e}")
            return {}

    def _save_with_lock(self, data: Dict[str, Any]) -> None:
        """
        Save preds.json (internal method; assumes lock is already held).

        Args:
            data: data dict to save
        """
        # Use temp file + atomic rename to avoid partial writes
        temp_path = self.preds_path.parent / f".{self.preds_path.name}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temp_path.replace(self.preds_path)

    def save(self, data: Dict[str, Any]) -> None:
        """
        Save the complete preds data (overwrite).

        Args:
            data: complete data dict to save: {instance_id: instance_data}
        """
        with self._file_lock():
            self._save_with_lock(data)

    def update_instance(
        self,
        instance_id: str,
        updates: Dict[str, Any],
        merge: bool = True
    ) -> None:
        """
        Update data for a single instance.

        Args:
            instance_id: instance ID
            updates: dict of fields to update
            merge: if True, deep-merge; if False, fully replace

        Example:
            # Merge update (default)
            manager.update_instance("django-11141", {
                "meta": {"pass_gold_patch_status": "success"}
            })

            # Full replacement
            manager.update_instance("django-11141", {
                "instance_id": "django-11141",
                "model_test_patch": "..."
            }, merge=False)
        """
        with self._file_lock():
            data = self.load()

            if instance_id in data:
                if merge:
                    # Deep merge
                    data[instance_id] = self._deep_merge(
                        data[instance_id],
                        updates
                    )
                else:
                    data[instance_id] = updates
            else:
                # New instance
                data[instance_id] = updates

            self._save_with_lock(data)

    def update_instance_nested(
        self,
        instance_id: str,
        nested_updates: Dict[str, Any]
    ) -> None:
        """
        Update an instance using nested keys (supports "meta.coverage_rate" format).

        Args:
            instance_id: instance ID
            nested_updates: dict with dot-separated nested keys

        Example:
            manager.update_instance_nested("django-11141", {
                "meta.pass_gold_patch_status": "success",
                "meta.coverage_rate": 0.95,
                "stage.-1.evaluation_info": {...}
            })
        """
        with self._file_lock():
            data = self.load()

            # Ensure instance exists
            if instance_id not in data:
                data[instance_id] = {"instance_id": instance_id}

            target = data[instance_id]

            # Apply nested updates
            for key, value in nested_updates.items():
                if "." in key:
                    self._set_nested_value(target, key, value)
                else:
                    target[key] = value

            self._save_with_lock(data)

    def get_instance(self, instance_id: str) -> Optional[Dict[str, Any]]:
        """
        Get data for a single instance.

        Args:
            instance_id: instance ID

        Returns:
            Instance data dict, or None if not found
        """
        data = self.load()
        return data.get(instance_id)

    def get_all_instances(self) -> Dict[str, Any]:
        """
        Get all instances.

        Returns:
            Dict of all instances: {instance_id: instance_data}
        """
        return self.load()

    def get_failed_test_gen(self) -> List[str]:
        """
        Return instance_ids where test generation failed.

        Failure is defined as:
        - model_test_patch is empty or whitespace-only

        Returns:
            List of failed instance_ids
        """
        data = self.load()
        failed = []

        for instance_id, pred in data.items():
            if not isinstance(pred, dict):
                continue

            # Only check whether model_test_patch is non-empty;
            # any non-empty content is considered a successful test generation
            if not pred.get("model_test_patch", "").strip():
                failed.append(instance_id)

        return failed

    def get_gold_patch_failures(self) -> List[str]:
        """
        Return instance_ids where the gold patch failed.

        Failure is defined as: meta['pass_gold_patch_status'] != 'success'

        Returns:
            List of failed instance_ids
        """
        data = self.load()
        failed = []

        for instance_id, pred in data.items():
            if not isinstance(pred, dict):
                continue

            meta = pred.get("meta", {})
            if meta.get("pass_gold_patch_status") != "success":
                failed.append(instance_id)

        return failed

    def get_low_coverage_instances(self, threshold: float = 1.0) -> List[str]:
        """
        Return instance_ids with coverage below the threshold.

        Args:
            threshold: coverage threshold (default 1.0, i.e., 100%)

        Returns:
            List of low-coverage instance_ids
        """
        data = self.load()
        low_cov = []

        for instance_id, pred in data.items():
            if not isinstance(pred, dict):
                continue

            meta = pred.get("meta", {})

            # Only consider instances where the gold patch passed
            if meta.get("pass_gold_patch_status") == "success":
                cov_rate = meta.get("coverage_rate", "unknown")

                if isinstance(cov_rate, (int, float)) and 0 < cov_rate < threshold:
                    low_cov.append(instance_id)

        return low_cov

    def instance_exists(self, instance_id: str) -> bool:
        """
        Check whether an instance exists.

        Args:
            instance_id: instance ID

        Returns:
            True if the instance exists, False otherwise
        """
        return instance_id in self.load()

    def delete_instance(self, instance_id: str) -> bool:
        """
        Delete an instance.

        Args:
            instance_id: instance ID

        Returns:
            True if deleted successfully, False if the instance did not exist
        """
        with self._file_lock():
            data = self.load()

            if instance_id in data:
                del data[instance_id]
                self._save_with_lock(data)
                return True

            return False

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get summary statistics.

        Returns:
            Dict containing various statistics
        """
        data = self.load()

        total = len(data)
        failed_test_gen = len(self.get_failed_test_gen())
        gold_failures = len(self.get_gold_patch_failures())
        low_coverage = len(self.get_low_coverage_instances())
        successful = sum(
            1 for pred in data.values()
            if isinstance(pred, dict) and
            pred.get("meta", {}).get("pass_gold_patch_status") == "success"
        )

        return {
            "total_instances": total,
            "failed_test_generation": failed_test_gen,
            "gold_patch_failures": gold_failures,
            "low_coverage_instances": low_coverage,
            "successful_instances": successful,
        }

    # ========== Private Helper Methods ==========

    def _deep_merge(self, base: Dict, updates: Dict) -> Dict:
        """
        Deep-merge two dicts.

        Args:
            base: base dict
            updates: dict with updates

        Returns:
            Merged dict
        """
        result = base.copy()

        for key, value in updates.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value

        return result

    def _set_nested_value(self, target: Dict, key: str, value: Any) -> None:
        """
        Set the value of a nested field (supports "meta.coverage_rate" format).

        Args:
            target: target dict
            key: nested key (dot-separated)
            value: value to set
        """
        parts = key.split(".")
        current = target

        # Traverse down to the second-to-last level
        for i, part in enumerate(parts[:-1]):
            # Handle array indices (e.g., "stage.-1")
            if part.startswith("-") or part.isdigit():
                index = int(part)
                if not isinstance(current, list):
                    raise ValueError(f"Cannot index non-list with {part}")
                current = current[index]
            else:
                if part not in current:
                    # Check whether the next level is an array index
                    next_part = parts[i + 1]
                    if next_part.startswith("-") or next_part.isdigit():
                        current[part] = []
                    else:
                        current[part] = {}
                current = current[part]

        # Set the value at the last level
        last_part = parts[-1]
        if last_part.startswith("-") or last_part.isdigit():
            index = int(last_part)
            if not isinstance(current, list):
                raise ValueError(f"Cannot index non-list with {last_part}")
            current[index] = value
        else:
            current[last_part] = value


# ========== Convenience Functions (optional) ==========

def quick_update(
    preds_path: Union[str, Path],
    instance_id: str,
    updates: Dict[str, Any]
) -> None:
    """
    Quickly update a single instance without creating a ResultManager object.

    Example:
        quick_update("result/preds.json", "django-11141", {
            "meta.pass_gold_patch_status": "success"
        })
    """
    manager = ResultManager(preds_path)
    manager.update_instance_nested(instance_id, updates)
