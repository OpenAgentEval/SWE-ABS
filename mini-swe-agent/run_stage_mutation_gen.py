#!/usr/bin/env python3
"""
Stage 2 Mutation Pipeline Automation Script

This script automates the Stage 2 mutation workflow:
1. Mutation Generation (mutation_gen) - Generate N sets of mutations, one set per directory
2. Initial Test Evaluation (init_test) - Test each set's mutations against initial tests
3. Mutation Judge (judge) - LLM judges each set's mutations

Output structure:
  {output_dir}/
    set1/preds.json   <- mutations from first generation run
    set2/preds.json   <- mutations from second generation run
    ...
    logs/             <- pipeline-level logs
    stage2_mutation_report.json
"""

import argparse
import json
import logging
import os
import pty
import select
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


# Pipeline phase definitions - order matters!
PIPELINE_PHASES = [
    "mutation_gen",   # Phase 1: Mutation generation (iterative, per-set)
    "init_test",      # Phase 2: Initial test evaluation (per-set)
    "judge",          # Phase 3: LLM judge (per-set)
]


def should_run_phase(phase_name: str, start_from_phase: Optional[str]) -> bool:
    """
        Determine whether a given phase should be run.

            Examples:
                >>> should_run_phase("mutation_gen", None)   # start from beginning, run all phases
                True
                >>> should_run_phase("mutation_gen", "init_test")  # skip mutation_gen
                False
                >>> should_run_phase("judge", "init_test")   # start from init_test, run judge
                True
    """
    if start_from_phase is None:
        return True
    if phase_name not in PIPELINE_PHASES or start_from_phase not in PIPELINE_PHASES:
        return True
    return PIPELINE_PHASES.index(phase_name) >= PIPELINE_PHASES.index(start_from_phase)


@dataclass
class Stage2Config:
    """Configuration for Stage 2 Mutation Pipeline automation"""

    # ========== Paths ==========
    output_dir: Path          # Base output directory; sets go in output_dir/set{i}/

    # Directory paths
    mini_swe_agent_dir: Path
    swe_bench_dir: Path
    swe_bench_pro_dir: Path

    # ========== Model Settings ==========
    model: str
    temperature: float
    workers: int
    benchmark: str  # "swebench" or "swebenchpro"

    # ========== Dataset Settings ==========
    subset: str = "verified"
    split: str = "test"

    # ========== Config Files ==========
    mutation_config_path: Path = None
    judge_config_path: Path = None

    # ========== Mutation Generation Settings ==========
    required_mutations_per_instance: int = 2  # Number of sets (set1, set2, ...)
    max_mutation_gen_iterations: int = 5      # Max retry loops for Phase 1

    # ========== Evaluation Settings ==========
    run_id: str = "stage2_mutation"
    max_eval_workers: int = 8

    # ========== Judge Settings ==========
    judge_models: Optional[List[str]] = None
    judge_times: int = 3
    judge_workers: int = 2

    # ========== Instance Selection ==========
    run_instance_file: Optional[Path] = None
    instance_ids: Optional[List[str]] = None  # Resolved list (None = no filter)

    # ========== Behavior Flags ==========
    fail_fast: bool = False
    start_from_phase: Optional[str] = None
    redo_existing: bool = False

    # ========== Timeouts ==========
    script_timeout: int = 7200  # 2 hours

    def get_set_dir(self, set_index: int) -> Path:
        """Get the output directory for a specific mutation set"""
        return self.output_dir / f"set{set_index}"

    def get_preds_path(self, set_index: int) -> Path:
        """Get the preds.json path for a specific mutation set"""
        return self.get_set_dir(set_index) / "preds.json"


class MutationTracker:
    """Tracks state of mutation generation and validation across multiple sets"""

    def __init__(self, config: Stage2Config, logger: logging.Logger):
        self.config = config
        self.logger = logger

    def load_set_preds(self, set_index: int) -> Optional[Dict[str, Any]]:
        """Load preds.json for a specific set. Returns None if not found or invalid."""
        preds_path = self.config.get_preds_path(set_index)
        if not preds_path.exists():
            return None
        try:
            with open(preds_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.logger.debug(f"Set {set_index}: loaded {len(data)} instances from preds.json")
            return data
        except json.JSONDecodeError as e:
            self.logger.error(f"Set {set_index}: Failed to parse preds.json: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Set {set_index}: Error reading preds.json: {e}")
            return None

    def get_instances_needing_mutation(
        self, set_index: int, all_instance_ids: Optional[List[str]]
    ) -> List[str]:
        """
        Find instances that don't have a valid mutation in the given set.

        A valid mutation = non-empty model_patch in set's preds.json.

        Note: Only call this when preds.json already exists for the set.
              Check get_preds_path(set_index).exists() before calling.
        """
        preds = self.load_set_preds(set_index)
        if preds is None:
            return []

        candidates = all_instance_ids if all_instance_ids is not None else list(preds.keys())
        return [
            iid for iid in candidates
            if iid not in preds or not preds[iid].get('model_patch', '').strip()
        ]

    def get_instances_needing_judge(
        self, set_index: int, all_instance_ids: Optional[List[str]]
    ) -> List[str]:
        """
        Find instances in a set that passed init test but haven't been judged.
        """
        preds = self.load_set_preds(set_index)
        if preds is None:
            return []

        candidates = all_instance_ids if all_instance_ids is not None else list(preds.keys())
        needing = []
        for iid in candidates:
            if iid not in preds:
                continue
            eval_info = preds[iid].get('evaluation_info', {})
            if eval_info.get('pass_init_test_status') == 'success':
                if 'judge_info' not in preds[iid]:
                    needing.append(iid)
        return needing

    def get_set_statistics(self, set_index: int) -> Dict[str, Any]:
        """Get statistics for a specific set"""
        preds = self.load_set_preds(set_index)
        if preds is None:
            return {'set_index': set_index, 'exists': False}

        stats: Dict[str, Any] = {
            'set_index': set_index,
            'exists': True,
            'total_instances': len(preds),
            'with_valid_mutation': 0,
            'init_test_passed': 0,
            'init_test_failed': 0,
            'judged_relevant_and_valid': 0,
            'judged_not_relevant': 0,
            'judged_not_valid': 0,
        }

        for iid, data in preds.items():
            if data.get('model_patch', '').strip():
                stats['with_valid_mutation'] += 1

            eval_info = data.get('evaluation_info', {})
            status = eval_info.get('pass_init_test_status')
            if status == 'success':
                stats['init_test_passed'] += 1
            elif status == 'fail':
                stats['init_test_failed'] += 1

            judge_info = data.get('judge_info', {})
            if judge_info:
                if judge_info.get('isrele') and judge_info.get('isvalid'):
                    stats['judged_relevant_and_valid'] += 1
                elif not judge_info.get('isrele'):
                    stats['judged_not_relevant'] += 1
                elif not judge_info.get('isvalid'):
                    stats['judged_not_valid'] += 1

        return stats

    def get_all_statistics(self) -> List[Dict[str, Any]]:
        """Get statistics for all sets"""
        return [
            self.get_set_statistics(i)
            for i in range(1, self.config.required_mutations_per_instance + 1)
        ]


class MutationExecutor:
    """Executes mutation-related scripts for each set"""

    def __init__(self, config: Stage2Config, logger: logging.Logger):
        self.config = config
        self.logger = logger

    def run_mutation_generation(
        self, set_index: int, instance_ids: Optional[List[str]] = None
    ) -> bool:
        """Execute mutation generation, writing to set{N}/ directory"""
        set_dir = self.config.get_set_dir(set_index)
        set_dir.mkdir(parents=True, exist_ok=True)

        if instance_ids is not None and len(instance_ids) == 0:
            self.logger.info(f"Set {set_index}: No instances to generate mutations for")
            return True

        cmd = self._build_mutation_gen_command(set_dir, instance_ids)
        count_str = f"{len(instance_ids)} instances" if instance_ids else "all instances (no filter)"
        self.logger.info(f"Set {set_index}: Running mutation generation for {count_str}")

        result = self._execute_command(cmd, f"mutation_gen_set{set_index}")
        return result.returncode == 0

    def run_init_test_evaluation(self, set_index: int) -> bool:
        """Execute init test evaluation for a specific set"""
        preds_path = self.config.get_preds_path(set_index)

        if not preds_path.exists():
            self.logger.warning(f"Set {set_index}: preds.json not found, skipping init test")
            return True

        run_id = f"{self.config.run_id}_set{set_index}"
        cmd = self._build_init_test_command(preds_path, run_id)

        self.logger.info(f"Set {set_index}: Running initial test evaluation (run_id={run_id})")

        result = self._execute_command(cmd, f"init_test_set{set_index}")
        return result.returncode == 0

    def run_mutation_judge(
        self, set_index: int, instance_ids: Optional[List[str]] = None
    ) -> bool:
        """Execute mutation judge for a specific set"""
        preds_path = self.config.get_preds_path(set_index)

        if not preds_path.exists():
            self.logger.warning(f"Set {set_index}: preds.json not found, skipping judge")
            return True

        cmd = self._build_judge_command(preds_path, instance_ids)
        count_str = f"{len(instance_ids)} instances" if instance_ids else "all instances"
        self.logger.info(f"Set {set_index}: Running mutation judge for {count_str}")

        result = self._execute_command(cmd, f"judge_set{set_index}")
        return result.returncode == 0

    def _build_mutation_gen_command(
        self, set_dir: Path, instance_ids: Optional[List[str]]
    ) -> List[str]:
        """Build command for mutation generation targeting a specific set directory"""
        cmd = [
            "python",
            "src/minisweagent/swe_abs_run/swebench_mutation.py",
            "--benchmark", self.config.benchmark,
            "--subset", self.config.subset,
            "--split", self.config.split,
            "--output", str(set_dir),
            "--workers", str(self.config.workers),
            "--model", self.config.model,
            "--config", str(self.config.mutation_config_path),
            "--temperature", str(self.config.temperature),
        ]

        # Instance selection priority: explicit IDs > run_instance_file > no filter
        if instance_ids:
            cmd.extend(["--instance_ids", ",".join(instance_ids)])
        elif self.config.run_instance_file:
            cmd.extend(["--run_instance_file", str(self.config.run_instance_file)])

        return cmd

    def _build_init_test_command(self, preds_path: Path, run_id: str) -> List[str]:
        """Build command for initial test evaluation"""
        return [
            "python", "-m", "swebench.runtest.run_evaluation",
            "--predictions_path", str(preds_path),
            "--max_workers", str(self.config.max_eval_workers),
            "--run_id", run_id,
            "--dataset_name", "princeton-nlp/SWE-bench_Verified",
            "--eval_mutation", "True",
        ]

    def _build_judge_command(
        self, preds_path: Path, instance_ids: Optional[List[str]]
    ) -> List[str]:
        """Build command for mutation judging"""
        cmd = [
            "python",
            "src/minisweagent/swe_abs_run/judge_vaild_mutation.py",
            "--subset", self.config.subset,
            "--split", self.config.split,
            "--benchmark", self.config.benchmark,
            "--mutation_res_file", str(preds_path),
            "--judge_mutatation_config_spec", str(self.config.judge_config_path),
            "--workers", str(self.config.judge_workers),
        ]

        if self.config.judge_models:
            cmd.extend(["--models", ",".join(self.config.judge_models)])

        if instance_ids:
            cmd.extend(["--instance_ids", ",".join(instance_ids)])

        return cmd

    def _execute_command(self, cmd: List[str], phase: str) -> subprocess.CompletedProcess:
        """Execute command with real-time output using PTY for progress bar support"""
        self.logger.info(f"Executing: {' '.join(cmd)}")

        # Determine working directory
        if phase.startswith("mutation_gen") or phase.startswith("judge"):
            cwd = self.config.mini_swe_agent_dir
        else:  # init_test
            cwd = self.config.swe_bench_pro_dir if self.config.benchmark == "swebenchpro" \
                else self.config.swe_bench_dir

        logs_dir = self.config.output_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / f"{phase}_{int(time.time())}.log"

        try:
            master_fd, slave_fd = pty.openpty()
            process = subprocess.Popen(
                cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=str(cwd),
                close_fds=True
            )
            os.close(slave_fd)

            with open(log_file, 'w', buffering=1) as log_f:
                while True:
                    ready, _, _ = select.select([master_fd], [], [], 0.1)
                    if ready:
                        try:
                            data = os.read(master_fd, 1024)
                            if not data:
                                break
                            text = data.decode('utf-8', errors='replace')
                            sys.stdout.write(text)
                            sys.stdout.flush()
                            log_f.write(text)
                            log_f.flush()
                        except OSError:
                            break
                    if process.poll() is not None:
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

            returncode = process.wait(timeout=self.config.script_timeout)
            os.close(master_fd)

            self.logger.info(f"{phase} completed with return code {returncode}")
            self.logger.info(f"Logs saved to {log_file}")
            return subprocess.CompletedProcess(args=cmd, returncode=returncode)

        except subprocess.TimeoutExpired:
            self.logger.error(f"{phase} timed out after {self.config.script_timeout}s")
            if 'process' in locals():
                process.kill()
            if 'master_fd' in locals():
                try:
                    os.close(master_fd)
                except OSError:
                    pass
            return subprocess.CompletedProcess(cmd, returncode=1)
        except Exception as e:
            self.logger.error(f"Error executing {phase}: {e}", exc_info=True)
            if 'process' in locals():
                try:
                    process.kill()
                except OSError:
                    pass
            if 'master_fd' in locals():
                try:
                    os.close(master_fd)
                except OSError:
                    pass
            return subprocess.CompletedProcess(cmd, returncode=1)


class Stage2Orchestrator:
    """Main orchestrator for Stage 2 Mutation Pipeline"""

    def __init__(self, config: Stage2Config):
        self.config = config
        self.logger = self._setup_logger()
        self.tracker = MutationTracker(config, self.logger)
        self.executor = MutationExecutor(config, self.logger)
        self.stats = {
            'mutation_gen_iterations': 0,
        }

    def run(self) -> bool:
        """Execute full Stage 2 Mutation Pipeline"""
        num_sets = self.config.required_mutations_per_instance
        self.logger.info("=" * 80)
        self.logger.info("Starting Stage 2 Mutation Pipeline Automation")
        self.logger.info("=" * 80)
        self.logger.info(f"Output directory: {self.config.output_dir}")
        self.logger.info(f"Model: {self.config.model}")
        self.logger.info(f"Workers: {self.config.workers}")
        self.logger.info(f"Benchmark: {self.config.benchmark}")
        self.logger.info(f"Required mutation sets: {num_sets} (set1 ~ set{num_sets})")

        if self.config.instance_ids:
            self.logger.info(f"Instance IDs: {len(self.config.instance_ids)} specified")
        elif self.config.run_instance_file:
            self.logger.info(f"Instance file: {self.config.run_instance_file}")
        else:
            self.logger.info("Instance selection: no filter (swebench_mutation.py default)")

        if self.config.start_from_phase:
            self.logger.info(f"Resuming from phase: {self.config.start_from_phase}")

        try:
            if should_run_phase("mutation_gen", self.config.start_from_phase):
                self.logger.info("\n" + "=" * 80)
                self.logger.info("PHASE 1: MUTATION GENERATION")
                self.logger.info("=" * 80)
                if not self._phase_mutation_generation():
                    self.logger.error("Phase 1 (mutation_gen) failed. Stopping pipeline.")
                    self._generate_final_report()
                    return False
            else:
                self.logger.info("\nSkipping Phase 1: Mutation Generation")

            if should_run_phase("init_test", self.config.start_from_phase):
                self.logger.info("\n" + "=" * 80)
                self.logger.info("PHASE 2: INITIAL TEST EVALUATION")
                self.logger.info("=" * 80)
                if not self._phase_init_test():
                    self.logger.error("Phase 2 (init_test) failed. Stopping pipeline.")
                    self._generate_final_report()
                    return False
            else:
                self.logger.info("\nSkipping Phase 2: Initial Test")

            if should_run_phase("judge", self.config.start_from_phase):
                self.logger.info("\n" + "=" * 80)
                self.logger.info("PHASE 3: MUTATION JUDGE")
                self.logger.info("=" * 80)
                if not self._phase_judge():
                    self.logger.error("Phase 3 (judge) failed. Stopping pipeline.")
                    self._generate_final_report()
                    return False
            else:
                self.logger.info("\nSkipping Phase 3: Judge")

            self._generate_final_report()
            return True

        except KeyboardInterrupt:
            self.logger.warning("\n\nInterrupted by user")
            self._generate_final_report()
            return False
        except Exception as e:
            self.logger.error(f"Fatal error: {e}", exc_info=True)
            return False

    def _phase_mutation_generation(self) -> bool:
        """
        Phase 1: Generate mutations for each set, retrying until all instances have
        a valid (non-empty) model_patch, or max iterations reached.

        For N required mutations, creates N sets: set1/, set2/, ..., setN/.
        Each set is an independent directory with its own preds.json.
        """
        num_sets = self.config.required_mutations_per_instance
        instance_ids = self.config.instance_ids  # None if no filter specified

        for iteration in range(1, self.config.max_mutation_gen_iterations + 1):
            self.stats['mutation_gen_iterations'] = iteration
            self.logger.info(f"\n{'=' * 60}")
            self.logger.info(
                f"Mutation Generation Iteration {iteration}/{self.config.max_mutation_gen_iterations}"
            )
            self.logger.info('=' * 60)

            all_sets_complete = True

            for set_index in range(1, num_sets + 1):
                preds_path = self.config.get_preds_path(set_index)

                if not preds_path.exists():
                    # Set hasn't been run yet - run for all specified instances
                    all_sets_complete = False
                    self.logger.info(
                        f"Set {set_index}: preds.json not found, running mutation generation"
                    )
                    success = self.executor.run_mutation_generation(set_index, instance_ids)
                else:
                    # preds.json exists - check for instances with empty/missing patches
                    instances_needing = self.tracker.get_instances_needing_mutation(
                        set_index, instance_ids
                    )
                    if not instances_needing:
                        self.logger.info(f"Set {set_index}: Complete")
                        continue

                    all_sets_complete = False
                    self.logger.info(
                        f"Set {set_index}: {len(instances_needing)} instances need retry"
                    )
                    for i, iid in enumerate(instances_needing[:5], 1):
                        self.logger.info(f"  {i}. {iid}")
                    if len(instances_needing) > 5:
                        self.logger.info(f"  ... and {len(instances_needing) - 5} more")

                    # Retry only for instances with missing mutations
                    success = self.executor.run_mutation_generation(set_index, instances_needing)

                if not success:
                    self.logger.warning(
                        f"Set {set_index}: Generation script failed in iteration {iteration}"
                    )
                    if self.config.fail_fast:
                        return False

                time.sleep(2)

            if all_sets_complete:
                self.logger.info(
                    f"\nAll {num_sets} sets complete after {iteration} iteration(s)"
                )
                return True

        # Final status after max iterations
        incomplete = [
            i for i in range(1, num_sets + 1)
            if not self.config.get_preds_path(i).exists()
            or self.tracker.get_instances_needing_mutation(i, instance_ids)
        ]

        if incomplete:
            self.logger.warning(
                f"Sets {incomplete} still incomplete after "
                f"{self.config.max_mutation_gen_iterations} iterations. "
                f"Continuing pipeline with available mutations."
            )

        self.logger.info("Mutation generation phase completed")
        return True

    def _phase_init_test(self) -> bool:
        """Phase 2: Run initial tests for each set"""
        num_sets = self.config.required_mutations_per_instance

        for set_index in range(1, num_sets + 1):
            self.logger.info(f"\nEvaluating Set {set_index}...")
            success = self.executor.run_init_test_evaluation(set_index)

            if not success:
                self.logger.error(f"Set {set_index}: Init test evaluation failed")
                if self.config.fail_fast:
                    return False

            time.sleep(2)

        # Report results per set
        self.logger.info(f"\n{'=' * 60}")
        self.logger.info("Initial Test Results by Set:")
        for set_index in range(1, num_sets + 1):
            stats = self.tracker.get_set_statistics(set_index)
            if stats.get('exists'):
                passed = stats.get('init_test_passed', 0)
                failed = stats.get('init_test_failed', 0)
                self.logger.info(f"  Set {set_index}: {passed} passed, {failed} failed")
            else:
                self.logger.info(f"  Set {set_index}: no data")
        self.logger.info('=' * 60)

        self.logger.info("Initial test evaluation phase completed")
        return True

    def _phase_judge(self) -> bool:
        """Phase 3: Judge mutations for each set that passed init test"""
        num_sets = self.config.required_mutations_per_instance
        instance_ids = self.config.instance_ids

        for set_index in range(1, num_sets + 1):
            instances_to_judge = self.tracker.get_instances_needing_judge(
                set_index, instance_ids
            )

            if not instances_to_judge:
                self.logger.info(f"Set {set_index}: No instances need judging")
                continue

            self.logger.info(f"\nJudging Set {set_index}: {len(instances_to_judge)} instances")
            success = self.executor.run_mutation_judge(set_index, instances_to_judge)

            if not success:
                self.logger.error(f"Set {set_index}: Mutation judge failed")
                if self.config.fail_fast:
                    return False

            time.sleep(2)

        # Report results per set
        self.logger.info(f"\n{'=' * 60}")
        self.logger.info("Judge Results by Set:")
        for set_index in range(1, num_sets + 1):
            stats = self.tracker.get_set_statistics(set_index)
            if stats.get('exists'):
                valid = stats.get('judged_relevant_and_valid', 0)
                not_rele = stats.get('judged_not_relevant', 0)
                not_valid = stats.get('judged_not_valid', 0)
                self.logger.info(
                    f"  Set {set_index}: {valid} relevant&valid, "
                    f"{not_rele} not relevant, {not_valid} not valid"
                )
            else:
                self.logger.info(f"  Set {set_index}: no data")
        self.logger.info('=' * 60)

        self.logger.info("Mutation judge phase completed")
        return True

    def _generate_final_report(self):
        """Generate and log final statistics across all sets"""
        all_stats = self.tracker.get_all_statistics()

        self.logger.info("\n" + "=" * 80)
        self.logger.info("STAGE 2 MUTATION PIPELINE FINAL REPORT")
        self.logger.info("=" * 80)
        self.logger.info(f"Generation iterations: {self.stats['mutation_gen_iterations']}")
        self.logger.info(f"Total sets: {self.config.required_mutations_per_instance}")
        self.logger.info("")

        for stats in all_stats:
            set_index = stats['set_index']
            if not stats.get('exists'):
                self.logger.info(f"Set {set_index}: no data")
                continue
            self.logger.info(f"Set {set_index}:")
            self.logger.info(f"  Total instances:         {stats['total_instances']}")
            self.logger.info(f"  With valid mutation:     {stats['with_valid_mutation']}")
            self.logger.info(f"  Init test passed:        {stats['init_test_passed']}")
            self.logger.info(f"  Init test failed:        {stats['init_test_failed']}")
            self.logger.info(f"  Judged relevant & valid: {stats['judged_relevant_and_valid']}")
            self.logger.info(f"  Judged not relevant:     {stats['judged_not_relevant']}")
            self.logger.info(f"  Judged not valid:        {stats['judged_not_valid']}")
            self.logger.info("")

        # Save report to JSON
        report_path = self.config.output_dir / "stage2_mutation_report.json"
        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(
                    {'pipeline_stats': self.stats, 'set_stats': all_stats},
                    f, indent=2, ensure_ascii=False
                )
            self.logger.info(f"Full report saved to: {report_path}")
        except Exception as e:
            self.logger.error(f"Failed to save report: {e}")

        self.logger.info("=" * 80)

    def _setup_logger(self) -> logging.Logger:
        """Setup logger with file and console handlers"""
        logger = logging.getLogger("Stage2Mutation")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        logger.addHandler(console_handler)

        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        logs_dir = self.config.output_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / "stage2_mutation.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        logger.addHandler(file_handler)

        logger.info(f"Logging to: {log_file}")
        return logger


def main():
    """Main entry point with CLI argument parsing"""
    parser = argparse.ArgumentParser(
        description="Stage 2 Mutation Pipeline for SWE-PLUS",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    # ========== Required Arguments ==========
    parser.add_argument("--output", "-o", type=str, default="result/res_mutation",
                        help="Base output directory (sets written to {output}/{run_id}/set1/, ...)")
    parser.add_argument("--model", "-m", type=str, required=True,
                        help="Model to use for mutation generation (e.g., zai/glm-4.7)")

    # ========== Optional Arguments ==========
    parser.add_argument("--benchmark", type=str, default="swebench",
                        choices=["swebench", "swebenchpro"],
                        help="Benchmark type")
    parser.add_argument("--temperature", type=float, default=1.0,
                        help="Model temperature for mutation generation")
    parser.add_argument("--workers", type=int, default=2,
                        help="Number of parallel workers for mutation generation")

    # ========== Dataset Settings ==========
    parser.add_argument("--subset", type=str, default="verified",
                        help="Dataset subset to use")
    parser.add_argument("--split", type=str, default="test",
                        help="Dataset split to use")

    # ========== Config Files ==========
    parser.add_argument("--mutation-config", type=str,
                        default="./src/minisweagent/config/extra/swebench_mutation.yaml",
                        help="Path to mutation config file")
    parser.add_argument("--judge-config", type=str,
                        default="./src/minisweagent/config/extra/mutation_judge.yaml",
                        help="Path to judge config file")

    # ========== Mutation Settings ==========
    parser.add_argument("--required-mutations", type=int, default=2,
                        help="Number of mutation sets to generate (2 = set1 + set2)")
    parser.add_argument("--max-mutation-iterations", type=int, default=5,
                        help="Max retry iterations for mutation generation phase")

    # ========== Evaluation Settings ==========
    parser.add_argument("--run-id", type=str, default="stage2_mutation",
                        help="Run ID prefix for evaluation (set index appended: stage2_mutation_set1)")
    parser.add_argument("--max-eval-workers", type=int, default=8,
                        help="Number of parallel workers for evaluation")

    # ========== Judge Settings ==========
    parser.add_argument("--judge-models", type=str, default=None,
                        help="Comma-separated list of models for judging (e.g., 'model1,model2')")
    parser.add_argument("--judge-times", type=int, default=3,
                        help="Number of times to run judge with same model")
    parser.add_argument("--judge-workers", type=int, default=2,
                        help="Number of parallel workers for judging")

    # ========== Instance Selection ==========
    parser.add_argument("--run-instance-file", type=str, default=None,
                        help="Path to file containing instance IDs to run")
    parser.add_argument("--instance-ids", type=str, default=None,
                        help="Comma-separated list of instance IDs to run")

    # ========== Behavior Flags ==========
    parser.add_argument("--fail-fast", action="store_true",
                        help="Stop pipeline on first unrecoverable failure")
    parser.add_argument("--start-from-phase", type=str,
                        choices=["mutation_gen", "init_test", "judge"],
                        default=None,
                        help="Resume from a specific phase (skipping earlier phases)")
    parser.add_argument("--redo-existing", action="store_true",
                        help="Redo existing mutations even if preds.json already exists")

    args = parser.parse_args()

    # Build paths
    # Final layout: {output}/{run_id}/set{N}/preds.json
    output_dir = (Path(args.output) / args.run_id).resolve()
    mini_swe_agent_dir = Path(__file__).parent.resolve()
    swe_bench_dir = (mini_swe_agent_dir.parent / "swe-bench").resolve()
    swe_bench_pro_dir = (mini_swe_agent_dir.parent / "SWE-bench_Pro-os").resolve()

    # Parse instance IDs
    instance_ids_list = None
    if args.instance_ids:
        instance_ids_list = [iid.strip() for iid in args.instance_ids.split(",") if iid.strip()]

    # Parse judge models
    judge_models_list = None
    if args.judge_models:
        judge_models_list = [m.strip() for m in args.judge_models.split(",") if m.strip()]

    # Build configuration
    config = Stage2Config(
        output_dir=output_dir,

        mini_swe_agent_dir=mini_swe_agent_dir,
        swe_bench_dir=swe_bench_dir,
        swe_bench_pro_dir=swe_bench_pro_dir,

        model=args.model,
        temperature=args.temperature,
        workers=args.workers,
        benchmark=args.benchmark,

        subset=args.subset,
        split=args.split,

        mutation_config_path=Path(args.mutation_config).resolve(),
        judge_config_path=Path(args.judge_config).resolve(),

        required_mutations_per_instance=args.required_mutations,
        max_mutation_gen_iterations=args.max_mutation_iterations,

        run_id=args.run_id,
        max_eval_workers=args.max_eval_workers,

        judge_models=judge_models_list,
        judge_times=args.judge_times,
        judge_workers=args.judge_workers,

        run_instance_file=Path(args.run_instance_file).resolve() if args.run_instance_file else None,
        instance_ids=instance_ids_list,

        fail_fast=args.fail_fast,
        start_from_phase=args.start_from_phase,
        redo_existing=args.redo_existing,
    )

    # Validate paths
    if not mini_swe_agent_dir.exists():
        print(f"Error: mini-swe-agent directory not found at {mini_swe_agent_dir}")
        sys.exit(1)
    if args.benchmark == "swebench" and not swe_bench_dir.exists():
        print(f"Error: swe-bench directory not found at {swe_bench_dir}")
        sys.exit(1)
    if args.benchmark == "swebenchpro" and not swe_bench_pro_dir.exists():
        print(f"Error: SWE-bench_Pro-os directory not found at {swe_bench_pro_dir}")
        sys.exit(1)

    # When resuming from init_test or judge, set directories must already exist
    if args.start_from_phase and args.start_from_phase != "mutation_gen":
        missing = [
            i for i in range(1, args.required_mutations + 1)
            if not config.get_preds_path(i).exists()
        ]
        if missing:
            sets_str = ", ".join(f"set{i}" for i in missing)
            print(f"Error: preds.json not found for: {sets_str}")
            print(f"When resuming from '{args.start_from_phase}', all sets must have preds.json.")
            sys.exit(1)

    # Run orchestrator
    orchestrator = Stage2Orchestrator(config)
    success = orchestrator.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
