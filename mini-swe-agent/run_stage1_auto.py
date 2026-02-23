#!/usr/bin/env python3
"""
Stage 1 Automation Script for SWE-ABS Pipeline

This script automates the entire Stage 1 workflow:
1. Test Generation (test_gen) - Generate tests with retry
2. Hard Code Fix (hard_code_fix) - Apply hard code fixes
3. Gold Patch Validation (gold_eval) - Validate with gold patches and retry
4. Coverage Fix (coverage_fix) - Agent generates improved test patches (optional)
5. Coverage Evaluation (coverage_eval) - Execute tests + verify + get coverage (optional)

Each phase can be skipped for checkpoint resume using --start-from-phase.

Author: Auto-generated based on STAGE1_AUTOMATION_PLAN.md
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


# Pipeline phase definitions - order matters!
PIPELINE_PHASES = [
    "test_gen",        # Phase 1: Test generation
    "hard_code_fix",   # Phase 2: Hard code fix
    "gold_eval",       # Phase 3: Gold patch validation (without coverage)
    "coverage_fix",    # Phase 4: Coverage fix (agent generates improved test patches)
    "coverage_eval",   # Phase 5: Coverage evaluation (run tests + verify gold patch + get coverage data)
]


def should_run_phase(phase_name: str, start_from_phase: Optional[str]) -> bool:
    """
        Determine whether a given phase should be run.

            Args:
                phase_name: Name of the current phase
                start_from_phase: Phase to start from (None means start from the beginning)

            Returns:
                True if should run, False if should skip

            Examples:
                >>> should_run_phase("test_gen", None)  # start from beginning, run all phases
                True
                >>> should_run_phase("test_gen", "hard_code_fix")  # start from hard_code_fix, skip test_gen
                False
                >>> should_run_phase("gold_eval", "hard_code_fix")  # start from hard_code_fix, run gold_eval
                True
    """
    if start_from_phase is None:
        return True

    if phase_name not in PIPELINE_PHASES:
        # Unknown stage name, run by default (backward compatibility)
        return True

    if start_from_phase not in PIPELINE_PHASES:
        # Invalid starting stage, default to beginning
        return True

    current_index = PIPELINE_PHASES.index(phase_name)
    start_index = PIPELINE_PHASES.index(start_from_phase)

    return current_index >= start_index


@dataclass
class Stage1Config:
    """Configuration for Stage 1 automation"""

    # Paths
    output_dir: Path
    preds_json_path: Path
    must_cover_line_file: Path

    # Directory paths
    mini_swe_agent_dir: Path
    swe_bench_dir: Path
    swe_bench_pro_dir: Path

    # Model settings
    model: str
    temperature: float
    workers: int
    benchmark: str  # "swebench" or "swebenchpro"

    # Test generation settings
    subset: str = "verified"
    split: str = "test"

    # Evaluation settings
    run_id: str = "stage1_auto"
    eval_timeout: int = 120
    max_eval_workers: int = 12

    # Retry limits
    max_test_gen_retries: int = 3
    max_hard_code_fix_retries: int = 3
    max_combined_retries: int = 2
    max_coverage_fix_attempts: int = 2

    # Behavior flags
    enable_coverage_fix: bool = True
    fail_fast: bool = False
    start_from_phase: Optional[str] = None  # Options: test_gen, hard_code_fix, gold_eval, coverage_fix, coverage_eval

    # Timeouts
    script_timeout: int = 7200  # 2 hours


class InstanceTracker:
    """Tracks state of all instances through the pipeline"""

    def __init__(self, preds_json_path: Path, logger: logging.Logger):
        self.preds_json_path = preds_json_path
        self.logger = logger

    def load_from_preds(self) -> Optional[Dict[str, Any]]:
        """Load and parse preds.json. Returns None if file doesn't exist or parsing fails."""
        if not self.preds_json_path.exists():
            self.logger.warning(f"preds.json not found at {self.preds_json_path}")
            return None

        try:
            with open(self.preds_json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.logger.debug(f"Loaded {len(data)} instances from preds.json")
            return data
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse preds.json: {e}")
            self.logger.error("This usually means the JSON file is corrupted. Check the logs for the test generation phase.")
            return None
        except Exception as e:
            self.logger.error(f"Error reading preds.json: {e}")
            return None

    def get_failed_test_gen(self) -> Optional[List[str]]:
        """Find instances with failed test generation. Returns None if preds.json cannot be loaded."""
        preds = self.load_from_preds()
        if preds is None:
            return None

        failed = []

        for instance_id, data in preds.items():
            # Check if model_test_patch is empty or missing
            model_test_patch = data.get('model_test_patch', '').strip()
            if not model_test_patch:
                failed.append(instance_id)
                continue

            # Check if latest stage has incomplete status
            stages = data.get('stage', [])
            if stages and isinstance(stages, list):
                last_stage = stages[-1]
                status = last_stage.get('status', '')
                if status != 'completed':
                    failed.append(instance_id)

        return failed

    def get_gold_patch_failures(self) -> Optional[List[str]]:
        """Find instances where gold patch validation failed. Returns None if preds.json cannot be loaded."""
        preds = self.load_from_preds()
        if preds is None:
            return None

        failed = []

        for instance_id, data in preds.items():
            meta = data.get('meta', {})
            pass_status = meta.get('pass_gold_patch_status', '')
            if pass_status != 'success':
                failed.append(instance_id)

        return failed

    def get_low_coverage_instances(self) -> Optional[List[str]]:
        """Find instances with coverage < 1.0 and gold patch passing. Returns None if preds.json cannot be loaded."""
        preds = self.load_from_preds()
        if preds is None:
            return None

        low_coverage = []

        for instance_id, data in preds.items():
            meta = data.get('meta', {})

            # Must pass gold patch first
            pass_status = meta.get('pass_gold_patch_status', '')
            if pass_status != 'success':
                continue

            # Check coverage rate
            coverage_rate = meta.get('coverage_rate', 'unknown')
            if isinstance(coverage_rate, (int, float)) and 0 < coverage_rate < 1.0:
                low_coverage.append(instance_id)

        return low_coverage

    def get_successful_instances(self) -> List[str]:
        """Get instances that passed gold patch. Returns empty list if preds.json cannot be loaded."""
        preds = self.load_from_preds()
        if preds is None:
            return []

        successful = []

        for instance_id, data in preds.items():
            meta = data.get('meta', {})
            pass_status = meta.get('pass_gold_patch_status', '')
            if pass_status == 'success':
                successful.append(instance_id)

        return successful

    def get_all_instances(self) -> List[str]:
        """Get all instance IDs from preds.json. Returns empty list if preds.json cannot be loaded."""
        preds = self.load_from_preds()
        if preds is None:
            return []
        return list(preds.keys())


class ScriptExecutor:
    """Executes shell scripts and captures results"""

    def __init__(self, config: Stage1Config, logger: logging.Logger):
        self.config = config
        self.logger = logger

    def run_test_generation(self, instance_ids: Optional[List[str]], redo_existing: bool = True) -> bool:
        """Execute run_agent.sh for specified instances (or all if instance_ids is None/empty)"""
        if instance_ids is not None and len(instance_ids) == 0:
            self.logger.info("No instances to generate tests for")
            return True

        cmd = self._build_test_gen_command(instance_ids, redo_existing)

        if instance_ids is None:
            self.logger.info(f"Running test generation for all instances")
        else:
            self.logger.info(f"Running test generation for {len(instance_ids)} instances")

        result = self._execute_command(cmd, "test_generation")
        return result.returncode == 0

    def run_hard_code_fix(self, instance_ids: Optional[List[str]] = None) -> bool:
        """Execute run_agent_fix.sh with Hard_Code_Fix"""
        cmd = self._build_fix_command("Hard_Code_Fix", instance_ids)

        if instance_ids:
            self.logger.info(f"Running Hard_Code_Fix for {len(instance_ids)} instances")
        else:
            self.logger.info("Running Hard_Code_Fix for all eligible instances")

        result = self._execute_command(cmd, "hard_code_fix")
        return result.returncode == 0

    def run_coverage_fix(self, instance_ids: Optional[List[str]] = None) -> bool:
        """Execute run_agent_fix.sh with Coverage_Fix"""
        cmd = self._build_fix_command("Coverage_Fix", instance_ids)

        if instance_ids:
            self.logger.info(f"Running Coverage_Fix for {len(instance_ids)} instances")
        else:
            self.logger.info("Running Coverage_Fix for all eligible instances")

        result = self._execute_command(cmd, "coverage_fix")
        return result.returncode == 0

    def run_gold_eval(self, instance_ids: Optional[List[str]] = None,
                     coverage_eval: bool = False) -> Dict[str, Any]:
        """Execute eval_gold.sh and return results"""
        cmd = self._build_eval_command(instance_ids, coverage_eval)

        if coverage_eval:
            self.logger.info("Running gold patch evaluation with coverage")
        else:
            self.logger.info("Running gold patch evaluation")

        result = self._execute_command(cmd, "gold_eval")
        return {"success": result.returncode == 0}

    def _build_test_gen_command(self, instance_ids: Optional[List[str]], redo_existing: bool) -> List[str]:
        """Build command for test generation - directly call Python script"""
        cmd = [
            "python",
            "src/minisweagent/swe_abs_run/swebench_test.py",
            "--benchmark", self.config.benchmark,
            "--subset", self.config.subset,
            "--split", self.config.split,
            "--model", self.config.model,
            "--output", str(self.config.output_dir),
            "--redo_existing", str(redo_existing).lower(),
            "--workers", str(self.config.workers),
            "--temperature", str(self.config.temperature),
        ]

        # Only add --run_instance if specific instances are provided
        if instance_ids is not None and len(instance_ids) > 0:
            cmd.extend(["--run_instance", ",".join(instance_ids)])

        return cmd

    def _build_fix_command(self, fix_type: str, instance_ids: Optional[List[str]]) -> List[str]:
        """Build command for fix script - directly call Python script"""
        cmd = [
            "python",
            "src/minisweagent/swe_abs_run/swebench_test_fix.py",
            "--aug_test_file", str(self.config.preds_json_path),
            "--workers", str(self.config.workers),
            "--model", self.config.model,
            "--temperature", str(self.config.temperature),
            "--fix_type", fix_type,
            "--benchmark", self.config.benchmark,
        ]

        if instance_ids:
            cmd.extend(["--instance_ids", ",".join(instance_ids)])

        return cmd

    def _build_eval_command(self, instance_ids: Optional[List[str]], coverage_eval: bool) -> List[str]:
        """Build command for evaluation - different for swebench vs swebenchpro"""

        if self.config.benchmark == "swebenchpro":
            # Use SWE-bench_Pro-os evaluation
            cmd = [
                "python", "-m", "run_test.eval_model_test_patch",
                "--input_path", str(self.config.preds_json_path),
                "--scripts_dir", "run_scripts",
                "--run_id", self.config.run_id,
                "--redo", "True",
                "--num_workers", str(self.config.max_eval_workers),
                "--must_cover_line", str(self.config.must_cover_line_file),
                "--rewrite_preds", "True",
                "--use_coverage", "True" if coverage_eval else "False",
                "--eval_gold_patch", "true",
                "--coverage_eval", "True" if coverage_eval else "False",
                "--use_local_docker",
                "--mem_limit", "4g",
            ]
        else:
            # Use swe-bench evaluation
            cmd = [
                "python", "-m", "swebench.runtest.run_evaluation_test",
                "--predictions_test_path", str(self.config.preds_json_path),
                "--max_workers", str(self.config.max_eval_workers),
                "--timeout", str(self.config.eval_timeout),
                "--rewrite_preds", "True",
                "--run_id", self.config.run_id,
                "--eval_gold_patch", "True",
                "--re_run_eval", "True",
                "--use_coverage", "True",
                "--must_cover_line", str(self.config.must_cover_line_file),
                "--coverage_eval", "True" if coverage_eval else "False",
            ]

        if instance_ids:
            cmd.extend(["--instance_ids", ",".join(instance_ids)])

        return cmd

    def _execute_command(self, cmd: List[str], phase: str) -> subprocess.CompletedProcess:
        """Execute command with real-time output and logging using PTY for progress bar support"""
        import pty
        import os
        import select

        self.logger.info(f"Executing: {' '.join(cmd)}")

        # Determine working directory based on phase
        if phase in ["test_generation", "hard_code_fix", "coverage_fix"]:
            cwd = self.config.mini_swe_agent_dir
        else:  # gold_eval
            if self.config.benchmark == "swebenchpro":
                cwd = self.config.swe_bench_pro_dir
            else:
                cwd = self.config.swe_bench_dir

        # Create logs directory and save log file there
        logs_dir = self.config.output_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / f"{phase}_{int(time.time())}.log"

        try:
            # Use PTY to make subprocess think it's running in a terminal
            # This allows Rich's Live and Progress bars to work properly
            master_fd, slave_fd = pty.openpty()

            # Start process with PTY
            process = subprocess.Popen(
                cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=str(cwd),
                close_fds=True
            )

            # Close slave fd in parent process (child has its own copy)
            os.close(slave_fd)

            # Open log file for writing
            with open(log_file, 'w', buffering=1) as log_f:
                # Read from master fd and write to both console and log file
                while True:
                    # Use select to check if data is available (with timeout)
                    ready, _, _ = select.select([master_fd], [], [], 0.1)

                    if ready:
                        try:
                            # Read available data
                            data = os.read(master_fd, 1024)
                            if not data:
                                break

                            # Decode and process output
                            text = data.decode('utf-8', errors='replace')

                            # Write to console (with ANSI codes for progress bars)
                            sys.stdout.write(text)
                            sys.stdout.flush()

                            # Write to log file (includes ANSI codes for completeness)
                            log_f.write(text)
                            log_f.flush()

                        except OSError:
                            # PTY closed
                            break

                    # Check if process has finished
                    if process.poll() is not None:
                        # Process finished, read any remaining data
                        try:
                            while True:
                                data = os.read(master_fd, 1024)
                                if not data:
                                    break
                                text = data.decode('utf-8', errors='replace')
                                sys.stdout.write(text)
                                sys.stdout.flush()
                                log_f.write(text)
                                log_f.flush()
                        except OSError:
                            pass
                        break

                # Wait for process to complete
                returncode = process.wait(timeout=self.config.script_timeout)

            # Close master fd
            os.close(master_fd)

            # Create result object
            result = subprocess.CompletedProcess(
                args=cmd,
                returncode=returncode,
                stdout=None,
                stderr=None
            )

            self.logger.info(f"{phase} completed with return code {result.returncode}")
            self.logger.info(f"Logs saved to {log_file}")

            return result

        except subprocess.TimeoutExpired:
            self.logger.error(f"{phase} timed out after {self.config.script_timeout}s")
            if 'process' in locals():
                process.kill()
            if 'master_fd' in locals():
                try:
                    os.close(master_fd)
                except:
                    pass
            return subprocess.CompletedProcess(cmd, returncode=1)
        except Exception as e:
            self.logger.error(f"Error executing {phase}: {e}", exc_info=True)
            if 'process' in locals():
                try:
                    process.kill()
                except:
                    pass
            if 'master_fd' in locals():
                try:
                    os.close(master_fd)
                except:
                    pass
            return subprocess.CompletedProcess(cmd, returncode=1)


class Stage1Orchestrator:
    """Main orchestrator for Stage 1 automation"""

    def __init__(self, config: Stage1Config):
        self.config = config
        self.logger = self._setup_logger()
        self.tracker = InstanceTracker(config.preds_json_path, self.logger)
        self.executor = ScriptExecutor(config, self.logger)
        self.stats = {
            'test_gen_iterations': 0,
            'hard_code_fix_iterations': 0,
            'coverage_fix_iterations': 0,
            'total_instances': 0,
            'successful_instances': 0,
            'failed_instances': 0,
            'permanently_failed': [],
        }

    def run(self) -> bool:
        """Execute full Stage 1 pipeline"""
        self.logger.info("="*80)
        self.logger.info("Starting Stage 1 Automation")
        self.logger.info("="*80)
        self.logger.info(f"Output directory: {self.config.output_dir}")
        self.logger.info(f"Model: {self.config.model}")
        self.logger.info(f"Workers: {self.config.workers}")
        self.logger.info(f"Benchmark: {self.config.benchmark}")

        if self.config.start_from_phase:
            self.logger.info(f"⚡ Resuming from phase: {self.config.start_from_phase}")

        try:
            # Phase 1: Test Generation
            if should_run_phase("test_gen", self.config.start_from_phase):
                self.logger.info("\n" + "="*80)
                self.logger.info("PHASE 1: TEST GENERATION")
                self.logger.info("="*80)

                if not self._phase_test_generation():
                    self.logger.error("✗ Phase 1 (test_gen) failed. Stopping pipeline.")
                    self._generate_final_report()
                    return False
            else:
                self.logger.info("\n⏭ Skipping Phase 1: Test Generation")

            # Phase 2: Hard Code Fix
            if should_run_phase("hard_code_fix", self.config.start_from_phase):
                self.logger.info("\n" + "="*80)
                self.logger.info("PHASE 2: HARD CODE FIX")
                self.logger.info("="*80)

                if not self._phase_hard_code_fix():
                    self.logger.error("✗ Phase 2 (hard_code_fix) failed. Stopping pipeline.")
                    self._generate_final_report()
                    return False
            else:
                self.logger.info("\n⏭ Skipping Phase 2: Hard Code Fix")

            # raise RuntimeError("debug")

            # Phase 3: Gold Patch Validation
            if should_run_phase("gold_eval", self.config.start_from_phase):
                self.logger.info("\n" + "="*80)
                self.logger.info("PHASE 3: GOLD PATCH VALIDATION")
                self.logger.info("="*80)

                if not self._phase_gold_eval():
                    self.logger.error("✗ Phase 3 (gold_eval) failed. Stopping pipeline.")
                    self._generate_final_report()
                    return False
            else:
                self.logger.info("\n⏭ Skipping Phase 3: Gold Patch Validation")

            # Phase 4: Coverage Fix (optional)
            if self.config.enable_coverage_fix:
                if should_run_phase("coverage_fix", self.config.start_from_phase):
                    self.logger.info("\n" + "="*80)
                    self.logger.info("PHASE 4: COVERAGE FIX (Agent Work)")
                    self.logger.info("="*80)

                    if not self._phase_coverage_fix():
                        self.logger.error("✗ Phase 4 (coverage_fix) failed. Stopping pipeline.")
                        self._generate_final_report()
                        return False
                else:
                    self.logger.info("\n⏭ Skipping Phase 4: Coverage Fix")

                # Phase 5: Coverage Evaluation (Test Execution)
                if should_run_phase("coverage_eval", self.config.start_from_phase):
                    self.logger.info("\n" + "="*80)
                    self.logger.info("PHASE 5: COVERAGE EVALUATION (Test Execution)")
                    self.logger.info("="*80)

                    if not self._phase_coverage_eval():
                        self.logger.error("✗ Phase 5 (coverage_eval) failed. Stopping pipeline.")
                        self._generate_final_report()
                        return False
                else:
                    self.logger.info("\n⏭ Skipping Phase 5: Coverage Evaluation")
            else:
                self.logger.info("\n⏭ Skipping Phase 4 & 5: Coverage Fix and Evaluation (disabled)")

            # Final report
            self._generate_final_report()

            return True

        except KeyboardInterrupt:
            self.logger.warning("\n\nInterrupted by user")
            self._generate_final_report()
            return False
        except Exception as e:
            self.logger.error(f"Fatal error: {e}", exc_info=True)
            return False

    def _phase_test_generation(self) -> bool:
        """Phase 1: Generate tests until all succeed or max retries"""

        # Check if preds.json exists, if not, run initial test generation
        if not self.config.preds_json_path.exists():
            self.logger.info("preds.json not found. Running initial test generation for all instances...")
            self.stats['test_gen_iterations'] = 1

            # Run test generation without specifying instance_ids (will process all)
            success = self.executor.run_test_generation(None, redo_existing=True)
            if not success:
                self.logger.error("✗ Initial test generation failed")
                return False

            # Wait for file system sync
            self.logger.info("Waiting 2 seconds for file system sync...")
            time.sleep(2)

            # Check if all instances succeeded after initial generation
            failed_ids = self.tracker.get_failed_test_gen()
            if failed_ids is None:
                self.logger.error("✗ Cannot load or parse preds.json after initial generation")
                return False

            if not failed_ids:
                self.logger.info("✓ All instances have successful test generation!")
                return True

            # Some instances failed, continue with retry logic
            self.logger.info(f"⚠ {len(failed_ids)} instances need retry")
            iteration_start = 2
        else:
            iteration_start = 1

        for iteration in range(iteration_start, self.config.max_test_gen_retries + 1):
            self.stats['test_gen_iterations'] = iteration
            self.logger.info(f"\n{'='*60}")
            self.logger.info(f"Test Generation Iteration {iteration}/{self.config.max_test_gen_retries}")
            self.logger.info('='*60)

            failed_ids = self.tracker.get_failed_test_gen()

            if failed_ids is None:
                self.logger.error("✗ Cannot load or parse preds.json - stopping test generation phase")
                self.logger.error("The JSON file may be corrupted. Check test_generation logs for errors.")
                return False

            if not failed_ids:
                self.logger.info("✓ All instances have successful test generation!")
                return True

            self.logger.info(f"Found {len(failed_ids)} instances needing test generation:")
            for i, instance_id in enumerate(failed_ids[:10], 1):
                self.logger.info(f"  {i}. {instance_id}")
            if len(failed_ids) > 10:
                self.logger.info(f"  ... and {len(failed_ids) - 10} more")

            # Run test generation for failed instances
            success = self.executor.run_test_generation(failed_ids, redo_existing=True)

            if not success:
                self.logger.warning(f"⚠ Test generation script failed in iteration {iteration}")

            # Wait for file system sync
            self.logger.info("Waiting 2 seconds for file system sync...")
            time.sleep(2)

        # Final check
        failed_ids = self.tracker.get_failed_test_gen()
        if failed_ids is None:
            self.logger.error("✗ Cannot load or parse preds.json after all retries")
            return False

        # Check if ALL instances failed
        all_instances = self.tracker.get_all_instances()
        if failed_ids and len(failed_ids) == len(all_instances):
            self.logger.error(f"✗ ALL {len(all_instances)} instances failed test generation")
            self.logger.error("✗ Cannot proceed to next phase without any successful test generation")
            return False

        if failed_ids:
            self.logger.warning(f"⚠ Test generation failed for {len(failed_ids)}/{len(all_instances)} instances")
            self.logger.warning("Continuing with successful instances...")
            self.stats['permanently_failed'].extend(failed_ids)

        self.logger.info(f"✓ Test generation phase completed with {len(all_instances) - len(failed_ids)} successful instances")
        return True

    def _phase_hard_code_fix(self) -> bool:
        """Phase 2: Run hard code fix on all instances"""

        self.logger.info("Running Hard_Code_Fix on all instances...")
        if not self.executor.run_hard_code_fix(instance_ids=None):
            self.logger.error("✗ Hard_Code_Fix script failed (script-level error)")
            self.logger.error("This indicates a serious problem (e.g., missing file, invalid arguments)")
            return False

        time.sleep(2)
        self.logger.info("✓ Hard code fix phase completed")
        return True

    def _phase_gold_eval(self) -> bool:
        """Phase 3: Gold patch validation with retries and combined re-gen+fix for persistent failures"""

        # Initial validation
        self.logger.info("Running initial gold patch validation...")
        eval_result = self.executor.run_gold_eval(coverage_eval=False)
        if not eval_result["success"]:
            self.logger.error("✗ Gold patch evaluation script failed (script-level error)")
            return False
        time.sleep(2)

        # Retry logic: re-run hard code fix for failures
        for iteration in range(1, self.config.max_hard_code_fix_retries + 1):
            self.stats['hard_code_fix_iterations'] = iteration

            failed_ids = self.tracker.get_gold_patch_failures()
            if failed_ids is None:
                self.logger.error("✗ Cannot load or parse preds.json - stopping gold eval phase")
                return False

            if not failed_ids:
                self.logger.info("✓ All instances pass gold patch validation!")
                return True

            self.logger.info(f"\n{'='*60}")
            self.logger.info(f"Gold Validation Retry {iteration}/{self.config.max_hard_code_fix_retries}")
            self.logger.info('='*60)
            self.logger.info(f"Found {len(failed_ids)} instances failing gold patch:")
            for i, instance_id in enumerate(failed_ids[:10], 1):
                self.logger.info(f"  {i}. {instance_id}")
            if len(failed_ids) > 10:
                self.logger.info(f"  ... and {len(failed_ids) - 10} more")

            # Re-run hard code fix for failed instances
            self.logger.info("Re-running Hard_Code_Fix for failed instances...")
            if not self.executor.run_hard_code_fix(instance_ids=failed_ids):
                self.logger.error("✗ Hard_Code_Fix script failed (script-level error)")
                return False
            time.sleep(2)

            # Re-validate
            self.logger.info("Re-validating...")
            eval_result = self.executor.run_gold_eval(instance_ids=failed_ids, coverage_eval=False)
            if not eval_result["success"]:
                self.logger.error("✗ Gold patch evaluation script failed (script-level error)")
                return False
            time.sleep(2)

        # Combined re-gen + re-fix for persistent failures
        failed_ids = self.tracker.get_gold_patch_failures()
        if failed_ids is None:
            self.logger.error("✗ Cannot load or parse preds.json")
            return False

        if failed_ids and self.config.max_combined_retries > 0:
            self.logger.info(f"\n{'='*60}")
            self.logger.info(f"Attempting combined re-generation + re-fix")
            self.logger.info(f"for {len(failed_ids)} persistent failures")
            self.logger.info('='*60)

            for combined_iter in range(1, self.config.max_combined_retries + 1):
                self.logger.info(f"\nCombined Re-gen+Fix Iteration {combined_iter}/{self.config.max_combined_retries}")

                # Step 1: Re-generate tests
                self.logger.info("Step 1: Re-generating tests...")
                if not self.executor.run_test_generation(failed_ids, redo_existing=True):
                    self.logger.error("✗ Test generation script failed (script-level error)")
                    return False
                time.sleep(2)

                # Step 2: Re-fix
                self.logger.info("Step 2: Re-applying hard code fix...")
                if not self.executor.run_hard_code_fix(instance_ids=failed_ids):
                    self.logger.error("✗ Hard_Code_Fix script failed (script-level error)")
                    return False
                time.sleep(2)

                # Step 3: Re-validate
                self.logger.info("Step 3: Re-validating...")
                eval_result = self.executor.run_gold_eval(instance_ids=failed_ids, coverage_eval=False)
                if not eval_result["success"]:
                    self.logger.error("✗ Gold patch evaluation script failed (script-level error)")
                    return False
                time.sleep(2)

                # Check if resolved
                failed_ids = self.tracker.get_gold_patch_failures()
                if failed_ids is None:
                    self.logger.error("✗ Cannot load or parse preds.json")
                    return False
                if not failed_ids:
                    self.logger.info("✓ All instances now pass gold patch validation!")
                    return True

        # Final check
        failed_ids = self.tracker.get_gold_patch_failures()
        if failed_ids is None:
            self.logger.error("✗ Cannot load or parse preds.json")
            return False

        # Check if ALL instances failed
        all_instances = self.tracker.get_all_instances()
        if failed_ids and len(failed_ids) == len(all_instances):
            self.logger.error(f"✗ ALL {len(all_instances)} instances failed gold patch validation")
            self.logger.error("✗ No instances passed - stopping pipeline")
            return False

        if failed_ids:
            self.logger.warning(f"⚠ Gold patch validation failed for {len(failed_ids)}/{len(all_instances)} instances")
            self.logger.warning("Continuing with successful instances...")
            self.stats['permanently_failed'].extend(failed_ids)

        self.logger.info(f"✓ Gold validation phase completed with {len(all_instances) - len(failed_ids)} successful instances")
        return True

    def _phase_coverage_fix(self) -> bool:
        """Phase 4: Agent generates improved test patches to increase coverage"""

        low_coverage_ids = self.tracker.get_low_coverage_instances()
        if low_coverage_ids is None:
            self.logger.error("✗ Cannot load or parse preds.json - stopping coverage fix phase")
            return False

        if not low_coverage_ids:
            self.logger.info("✓ No instances need coverage fixing (all at 100% or unknown)")
            return True

        self.logger.info(f"Found {len(low_coverage_ids)} instances with coverage < 100%:")
        for i, instance_id in enumerate(low_coverage_ids[:10], 1):
            self.logger.info(f"  {i}. {instance_id}")
        if len(low_coverage_ids) > 10:
            self.logger.info(f"  ... and {len(low_coverage_ids) - 10} more")

        # Run coverage fix (agent work only - no test execution here)
        self.logger.info("Running coverage fix (agent generating improved test patches)...")
        if not self.executor.run_coverage_fix(instance_ids=low_coverage_ids):
            self.logger.error("✗ Coverage_Fix script failed (script-level error)")
            return False

        time.sleep(2)
        self.logger.info("✓ Coverage fix phase completed (test patches generated)")
        self.logger.info("→ Next: Run Phase 5 (coverage_eval) to execute tests and verify coverage")
        return True

    def _phase_coverage_eval(self) -> bool:
        """Phase 5: Execute tests + verify gold patch + get coverage data"""

        self.logger.info("Running tests with coverage evaluation...")
        self.logger.info("This will:")
        self.logger.info("  1. Execute all test patches")
        self.logger.info("  2. Verify they pass gold patch validation")
        self.logger.info("  3. Collect coverage data")

        # Run evaluation with coverage (this executes tests + verifies gold patch + gets coverage)
        eval_result = self.executor.run_gold_eval(coverage_eval=True)
        if not eval_result["success"]:
            self.logger.error("✗ Coverage evaluation failed (script-level error)")
            return False
        time.sleep(2)

        # Check gold patch results
        failed_ids = self.tracker.get_gold_patch_failures()
        if failed_ids is None:
            self.logger.error("✗ Cannot load or parse preds.json")
            return False

        if failed_ids:
            self.logger.warning(f"⚠ Warning: {len(failed_ids)} instances failed gold patch validation")
            for i, instance_id in enumerate(failed_ids[:10], 1):
                self.logger.warning(f"  {i}. {instance_id}")
            if len(failed_ids) > 10:
                self.logger.warning(f"  ... and {len(failed_ids) - 10} more")

        # Check coverage results
        low_coverage_ids = self.tracker.get_low_coverage_instances()
        if low_coverage_ids is None:
            self.logger.error("✗ Cannot load or parse preds.json")
            return False

        if low_coverage_ids:
            self.logger.info(f"⚠ {len(low_coverage_ids)} instances still have incomplete coverage")
        else:
            self.logger.info("✓ All instances achieved full coverage!")

        all_instances = self.tracker.get_all_instances()
        passed_count = len(all_instances) - len(failed_ids) if failed_ids else len(all_instances)
        self.logger.info(f"✓ Coverage evaluation completed: {passed_count}/{len(all_instances)} instances passing")

        return True

    def _generate_final_report(self):
        """Generate and log final statistics"""
        successful = self.tracker.get_successful_instances()
        failed = self.tracker.get_gold_patch_failures()
        low_coverage = self.tracker.get_low_coverage_instances()

        # Handle None cases (JSON parse errors) - use empty lists as fallback for reporting
        # Note: get_successful_instances() already returns [] on error, so only check failed and low_coverage
        if failed is None:
            self.logger.warning("⚠ Cannot load preds.json for failed instances - using empty list")
            failed = []
        if low_coverage is None:
            self.logger.warning("⚠ Cannot load preds.json for low coverage instances - using empty list")
            low_coverage = []

        self.stats['successful_instances'] = len(successful)
        self.stats['failed_instances'] = len(failed)
        self.stats['total_instances'] = len(self.tracker.get_all_instances())

        self.logger.info("\n" + "="*80)
        self.logger.info("STAGE 1 AUTOMATION FINAL REPORT")
        self.logger.info("="*80)
        self.logger.info(f"Total Instances: {self.stats['total_instances']}")
        self.logger.info(f"Test Generation Iterations: {self.stats['test_gen_iterations']}")
        self.logger.info(f"Hard Code Fix Iterations: {self.stats['hard_code_fix_iterations']}")
        self.logger.info(f"Coverage Fix Iterations: {self.stats['coverage_fix_iterations']}")
        self.logger.info("")
        self.logger.info(f"✓ Successful Instances (Gold Patch Pass): {len(successful)}")
        self.logger.info(f"✗ Failed Instances (Gold Patch Fail): {len(failed)}")
        self.logger.info(f"⚠ Low Coverage Instances: {len(low_coverage)}")

        if successful:
            success_rate = (len(successful) / self.stats['total_instances']) * 100
            self.logger.info(f"\nSuccess Rate: {success_rate:.1f}%")

        if failed:
            self.logger.info("\n" + "-"*60)
            self.logger.info("Failed Instance IDs:")
            self.logger.info("-"*60)
            for i, instance_id in enumerate(failed[:20], 1):
                self.logger.info(f"  {i}. {instance_id}")
            if len(failed) > 20:
                self.logger.info(f"  ... and {len(failed) - 20} more")

        if low_coverage:
            self.logger.info("\n" + "-"*60)
            self.logger.info("Low Coverage Instance IDs:")
            self.logger.info("-"*60)
            for i, instance_id in enumerate(low_coverage[:20], 1):
                self.logger.info(f"  {i}. {instance_id}")
            if len(low_coverage) > 20:
                self.logger.info(f"  ... and {len(low_coverage) - 20} more")

        # Save report to JSON
        report_path = self.config.output_dir / "stage1_automation_report.json"
        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'statistics': self.stats,
                    'successful_instances': successful,
                    'failed_instances': failed,
                    'low_coverage_instances': low_coverage,
                }, f, indent=2, ensure_ascii=False)

            self.logger.info(f"\n✓ Full report saved to: {report_path}")
        except Exception as e:
            self.logger.error(f"Failed to save report: {e}")

        self.logger.info("="*80)

    def _setup_logger(self) -> logging.Logger:
        """Setup logger with file and console handlers"""
        logger = logging.getLogger("Stage1Automation")
        logger.setLevel(logging.DEBUG)

        # Remove existing handlers
        logger.handlers.clear()

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

        # File handler - save to logs subdirectory
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        logs_dir = self.config.output_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / "stage1_automation.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        logger.info(f"Logging to: {log_file}")

        return logger


def main():
    """Main entry point with CLI argument parsing"""
    parser = argparse.ArgumentParser(
        description="Stage 1 Automation for SWE-PLUS Pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    # Required arguments
    parser.add_argument("--output", "-o", type=str, required=True,
                       help="Output directory (contains preds.json)")
    parser.add_argument("--model", "-m", type=str, required=True,
                       help="Model to use (e.g., zai/glm-4.7)")

    # Optional arguments
    parser.add_argument("--benchmark", type=str, default="swebench",
                       choices=["swebench", "swebenchpro"],
                       help="Benchmark type: swebench or swebenchpro")
    parser.add_argument("--temperature", type=float, default=1.0,
                       help="Model temperature")
    parser.add_argument("--workers", type=int, default=2,
                       help="Number of parallel workers for test generation and fixing")

    # Test generation settings
    parser.add_argument("--subset", type=str, default="verified",
                       help="Dataset subset to use")
    parser.add_argument("--split", type=str, default="test",
                       help="Dataset split to use")
    parser.add_argument("--repo-select-num", type=int, default=5,
                       help="Number of instances to select per repo")

    # Evaluation settings
    parser.add_argument("--run-id", type=str, default="stage1_auto",
                       help="Run ID for evaluation results")
    parser.add_argument("--eval-timeout", type=int, default=120,
                       help="Timeout for each evaluation (swebench only)")
    parser.add_argument("--max-eval-workers", type=int, default=12,
                       help="Number of parallel workers for evaluation")

    # Retry limits
    parser.add_argument("--max-test-gen-retries", type=int, default=3,
                       help="Max retries for test generation")
    parser.add_argument("--max-hard-code-fix-retries", type=int, default=3,
                       help="Max retries for hard code fix")
    parser.add_argument("--max-combined-retries", type=int, default=2,
                       help="Max combined re-gen + re-fix cycles")
    parser.add_argument("--max-coverage-fix-attempts", type=int, default=2,
                       help="Max attempts for coverage fixing")

    # Behavior flags
    parser.add_argument("--skip-coverage-fix", action="store_true",
                       help="Skip coverage fix phase")
    parser.add_argument("--fail-fast", action="store_true",
                       help="Stop on first unrecoverable failure")
    parser.add_argument("--start-from-phase", type=str,
                       choices=["test_gen", "hard_code_fix", "gold_eval", "coverage_fix", "coverage_eval"],
                       default=None,
                       help="Resume from a specific phase (test_gen, hard_code_fix, gold_eval, coverage_fix, or coverage_eval)")

    # Paths (with defaults)
    parser.add_argument("--must-cover-line-file", type=str,
                       default=None,
                       help="Path to must cover line file (auto-detected based on benchmark if not specified)")

    args = parser.parse_args()

    # Build configuration
    base_output_dir = Path(args.output)
    # Use run_id as subdirectory for better organization
    output_dir = base_output_dir / args.run_id
    
    mini_swe_agent_dir = Path(__file__).parent  # Directory where this script is located
    swe_bench_dir = mini_swe_agent_dir.parent / "swe-bench"
    swe_bench_pro_dir = mini_swe_agent_dir.parent / "SWE-bench_Pro-os"

    # Auto-detect must_cover_line_file based on benchmark if not specified
    if args.must_cover_line_file is None:
        if args.benchmark == "swebenchpro":
            must_cover_line_file = swe_bench_pro_dir / "swe_plus_res" / "extract_line_numbers" / "exe_line_all" / "final_results.json"
        else:
            must_cover_line_file = swe_bench_dir / "swe_plus_res" / "modified_raleted_lines" / "final_results.json"
    else:
        must_cover_line_file = Path(args.must_cover_line_file)

    config = Stage1Config(
        output_dir=output_dir.resolve(),  # Convert to absolute path
        preds_json_path=(output_dir / "preds.json").resolve(),  # Convert to absolute path
        must_cover_line_file=must_cover_line_file.resolve(),  # Convert to absolute path

        # Directory paths
        mini_swe_agent_dir=mini_swe_agent_dir.resolve(),  # Convert to absolute path
        swe_bench_dir=swe_bench_dir.resolve(),  # Convert to absolute path
        swe_bench_pro_dir=swe_bench_pro_dir.resolve(),  # Convert to absolute path

        # Model settings
        model=args.model,
        temperature=args.temperature,
        workers=args.workers,
        benchmark=args.benchmark,

        # Test generation settings
        subset=args.subset,
        split=args.split,

        # Evaluation settings
        run_id=args.run_id,
        eval_timeout=args.eval_timeout,
        max_eval_workers=args.max_eval_workers,

        # Retry limits
        max_test_gen_retries=args.max_test_gen_retries,
        max_hard_code_fix_retries=args.max_hard_code_fix_retries,
        max_combined_retries=args.max_combined_retries,
        max_coverage_fix_attempts=args.max_coverage_fix_attempts,

        # Behavior
        enable_coverage_fix=not args.skip_coverage_fix,
        fail_fast=args.fail_fast,
        start_from_phase=args.start_from_phase,
    )

    # Validate paths
    if not mini_swe_agent_dir.exists():
        print(f"Error: mini-swe-agent directory not found at {mini_swe_agent_dir}")
        sys.exit(1)
    if not swe_bench_dir.exists() and args.benchmark == "swebench":
        print(f"Error: swe-bench directory not found at {swe_bench_dir}")
        sys.exit(1)
    if not swe_bench_pro_dir.exists() and args.benchmark == "swebenchpro":
        print(f"Error: SWE-bench_Pro-os directory not found at {swe_bench_pro_dir}")
        sys.exit(1)

    # Run orchestrator
    orchestrator = Stage1Orchestrator(config)
    success = orchestrator.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()