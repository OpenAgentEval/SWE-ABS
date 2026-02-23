#!/usr/bin/env python3
"""
Stage 3 Augmented Test Pipeline Automation Script

This script connects Stage 1 (aug test generation) and Stage 2 (mutation generation):
1. Merge (merge)         - Merge Stage1 + Stage2 preds to create pred_mutation.json
2. No-Equ Aug (aug_no_equ) - Generate + validate aug tests for no-equivalent mutations
3. Equ Aug (aug_equ)       - Generate + validate aug tests for equivalent mutations

Each aug phase runs up to `required_mutations` iterations, each with up to
`max_aug_retries` retry loops (gen → eval → check → retry if needed).

Output structure:
  {output_dir}/
    pred_mutation.json                       <- merge output (copied from Stage1 dir)
    preds_no_equ_mutation_aug_0.json         <- aug gen iter=0
    preds_no_equ_mutation_aug_0_eval.json    <- aug eval iter=0
    preds_no_equ_mutation_aug_1.json         <- aug gen iter=1
    preds_no_equ_mutation_aug_1_eval.json    <- aug eval iter=1
    preds_equ_mutation_aug_0.json
    preds_equ_mutation_aug_0_eval.json
    ...
    logs/
    stage3_aug_report.json
"""

import argparse
import json
import logging
import os
import pty
import select
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# Pipeline phase definitions - order matters!
PIPELINE_PHASES = [
    "merge",       # Phase 1: Merge Stage1 + Stage2 → pred_mutation.json
    "aug_no_equ",  # Phase 2: Aug test for no-equivalent mutations (stage_name=no_equ_mutation_aug)
    "aug_equ",     # Phase 3: Aug test for equivalent mutations (stage_name=equ_mutation_aug)
]

# Mapping from pipeline phase to aug stage_name and use_key
AUG_STAGE_CONFIG = {
    "aug_no_equ": {
        "stage_name": "no_equ_mutation_aug",
        "use_key": "run_success_no_equ",
    },
    "aug_equ": {
        "stage_name": "equ_mutation_aug",
        "use_key": "run_fail_equ",
    },
}


def should_run_phase(phase_name: str, start_from_phase: Optional[str]) -> bool:
    """
        Determine whether a given phase should be run.

            Examples:
                >>> should_run_phase("merge", None)         # start from beginning, run all phases
                True
                >>> should_run_phase("merge", "aug_no_equ") # skip merge
                False
                >>> should_run_phase("aug_equ", "aug_no_equ")  # start from aug_no_equ, run aug_equ
                True
    """
    if start_from_phase is None:
        return True
    if phase_name not in PIPELINE_PHASES or start_from_phase not in PIPELINE_PHASES:
        return True
    return PIPELINE_PHASES.index(phase_name) >= PIPELINE_PHASES.index(start_from_phase)


@dataclass
class Stage3Config:
    """Configuration for Stage 3 Aug Test Pipeline"""

    # ========== Paths ==========
    output_dir: Path             # result/mutation_aug/{run_id}
    stage1_preds_path: Path      # Stage1 preds.json (used as --predictions_test_path for merge)
    stage2_output_dir: Path      # Stage2 base dir (contains set1/, set2/, ...)

    mini_swe_agent_dir: Path
    swe_bench_dir: Path

    # ========== Model Settings ==========
    model: str
    temperature: float = 0.0
    aug_workers: int = 2
    eval_workers: int = 8

    # ========== Dataset Settings ==========
    benchmark: str = "swebench"
    subset: str = "verified"
    split: str = "test"

    # ========== Config Files ==========
    aug_config_path: Optional[Path] = None  # swebench_aug_from_mutation.yaml

    # ========== Pipeline Settings ==========
    required_mutations: int = 2    # Number of iterations (iterations 0..N-1)
    max_aug_retries: int = 3       # Max retry loops per iteration

    # ========== Run Settings ==========
    run_id: str = "stage3_aug"
    fail_fast: bool = False
    start_from_phase: Optional[str] = None

    # ========== Instance Selection ==========
    instance_ids: Optional[List[str]] = None
    run_instance_file: Optional[Path] = None

    # ========== Timeouts ==========
    script_timeout: int = 7200  # 2 hours

    def get_aug_preds_path(self, stage_name: str, iteration: int) -> Path:
        """Path to aug gen output for a given stage/iteration"""
        return self.output_dir / f"preds_{stage_name}_{iteration}.json"

    def get_aug_eval_path(self, stage_name: str, iteration: int) -> Path:
        """Path to aug eval output for a given stage/iteration"""
        return self.output_dir / f"preds_{stage_name}_{iteration}_eval.json"

    def get_pred_mutation_path(self) -> Path:
        """Path to merged pred_mutation.json in output_dir"""
        return self.output_dir / "pred_mutation.json"

    def get_stage2_set_paths(self) -> List[Path]:
        """Discover all existing set preds.json paths from Stage2 output dir"""
        paths = []
        for i in range(1, self.required_mutations + 1):
            p = self.stage2_output_dir / f"set{i}" / "preds.json"
            if p.exists():
                paths.append(p)
        return paths


class AugTracker:
    """Tracks state of aug generation and evaluation"""

    def __init__(self, config: Stage3Config, logger: logging.Logger):
        self.config = config
        self.logger = logger

    def load_json(self, path: Path) -> Optional[Dict[str, Any]]:
        """Load a JSON file, return None if not found or invalid."""
        if not path.exists():
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse {path}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Error reading {path}: {e}")
            return None

    def get_instances_needing_retry(self, stage_name: str, iteration: int) -> List[str]:
        """
            Find instances that need to be retried from *_eval.json:
                    1. model_test_patch == "" (aug gen produced no output)
                    2. mutation_aug_evaluation_info.mutation_info[use_key] is non-empty (eval failed to catch the mutation)

                    Returns empty list if eval file doesn't exist.
        """
        eval_path = self.config.get_aug_eval_path(stage_name, iteration)
        eval_preds = self.load_json(eval_path)
        if eval_preds is None:
            return []

        use_key = AUG_STAGE_CONFIG.get(
            self._phase_from_stage_name(stage_name), {}
        ).get("use_key", "run_success_no_equ")

        needing_retry = []
        for iid, data in eval_preds.items():
            # Condition 1: aug gen didn't produce output
            if not data.get('model_test_patch', '').strip():
                needing_retry.append(iid)
                continue
            # Condition 2: aug eval didn't differentiate gold vs mutation
            aug_eval = data.get('mutation_aug_evaluation_info', {})
            mutation_info = aug_eval.get('mutation_info', {})
            if mutation_info.get(use_key):
                needing_retry.append(iid)

        return needing_retry

    def _phase_from_stage_name(self, stage_name: str) -> str:
        for phase, cfg in AUG_STAGE_CONFIG.items():
            if cfg["stage_name"] == stage_name:
                return phase
        return ""

    def get_aug_statistics(self, stage_name: str, iteration: int) -> Dict[str, Any]:
        """Read *_eval.json and compute basic statistics"""
        eval_path = self.config.get_aug_eval_path(stage_name, iteration)
        eval_preds = self.load_json(eval_path)
        if eval_preds is None:
            return {'exists': False, 'stage_name': stage_name, 'iteration': iteration}

        use_key = AUG_STAGE_CONFIG.get(
            self._phase_from_stage_name(stage_name), {}
        ).get("use_key", "run_success_no_equ")

        total = len(eval_preds)
        no_patch = 0
        still_not_caught = 0
        success = 0

        for data in eval_preds.values():
            if not data.get('model_test_patch', '').strip():
                no_patch += 1
            else:
                aug_eval = data.get('mutation_aug_evaluation_info', {})
                if aug_eval.get('mutation_info', {}).get(use_key):
                    still_not_caught += 1
                else:
                    success += 1

        return {
            'exists': True,
            'stage_name': stage_name,
            'iteration': iteration,
            'total': total,
            'success': success,
            'no_patch': no_patch,
            'still_not_caught': still_not_caught,
        }


class AugExecutor:
    """Executes aug-related scripts"""

    def __init__(self, config: Stage3Config, logger: logging.Logger):
        self.config = config
        self.logger = logger

    def run_merge(self) -> bool:
        """
        Step 1: Call run_evaluation_test.py to merge Stage1 + Stage2 preds.
        Output: {stage1_preds_dir}/preds_mutation.json
        Then copy to {output_dir}/pred_mutation.json.
        """
        set_paths = self.config.get_stage2_set_paths()
        if not set_paths:
            self.logger.error("No Stage2 set preds.json found. Cannot run merge.")
            return False

        mutation_paths_str = ",".join(str(p) for p in set_paths)
        run_id = f"{self.config.run_id}_merge"

        cmd = [
            "python", "-m", "swebench.runtest.run_evaluation_test",
            "--predictions_test_path", str(self.config.stage1_preds_path),
            "--max_workers", str(self.config.eval_workers),
            "--timeout", "120",
            "--run_id", run_id,
            "--mutation_paths", mutation_paths_str,
            "--rewrite_preds", "True",
            "--re_run_eval", "False",
        ]

        self.logger.info(f"Running merge: {len(set_paths)} mutation set(s)")
        self.logger.info(f"  Stage1 preds: {self.config.stage1_preds_path}")
        self.logger.info(f"  Mutation paths: {mutation_paths_str}")

        result = self._execute_command(cmd, "merge", cwd=self.config.swe_bench_dir)
        if result.returncode != 0:
            return False

        # Copy output to output_dir/pred_mutation.json
        stage1_dir = self.config.stage1_preds_path.parent
        stem, ext = self.config.stage1_preds_path.stem, self.config.stage1_preds_path.suffix
        source = stage1_dir / f"{stem}_mutation{ext}"
        dest = self.config.get_pred_mutation_path()

        if not source.exists():
            self.logger.error(f"Expected merge output not found: {source}")
            return False

        try:
            shutil.copy2(source, dest)
            self.logger.info(f"Copied merge output to: {dest}")
        except Exception as e:
            self.logger.error(f"Failed to copy merge output: {e}")
            return False

        return True

    def run_aug_gen(
        self,
        stage_name: str,
        iteration: int,
        aug_input: Path,
        retry_ids: Optional[List[str]] = None,
    ) -> bool:
        """
        Call swebench_aug_mutation.py to generate aug tests.
        Output: {output_dir}/preds_{stage_name}_{iteration}.json
        """
        is_retry = retry_ids is not None

        cmd = [
            "python",
            "src/minisweagent/swe_abs_run/swebench_aug_mutation.py",
            "--aug_test_file", str(aug_input),
            "--output", str(self.config.output_dir),
            "--workers", str(self.config.aug_workers),
            "--config", str(self.config.aug_config_path),
            "--model", self.config.model,
            "--stage_name", stage_name,
            "--iteration", str(iteration),
            "--temperature", str(self.config.temperature),
            "--benchmark", self.config.benchmark,
            "--redo_fail_instance", "true" if is_retry else "false",
        ]

        if retry_ids:
            cmd.extend(["--instance_ids", ",".join(retry_ids)])
        elif self.config.instance_ids:
            cmd.extend(["--instance_ids", ",".join(self.config.instance_ids)])
        elif self.config.run_instance_file:
            cmd.extend(["--run_instance_file", str(self.config.run_instance_file)])

        retry_str = f" (retry: {len(retry_ids)} instances)" if is_retry else ""
        self.logger.info(
            f"Running aug gen: stage={stage_name} iter={iteration}{retry_str}"
        )

        result = self._execute_command(
            cmd, f"aug_gen_{stage_name}_{iteration}", cwd=self.config.mini_swe_agent_dir
        )
        return result.returncode == 0

    def run_aug_eval(self, stage_name: str, iteration: int) -> bool:
        """
        Call run_evaluation_test_mutation_aug.py to validate aug tests.
        Input:  {output_dir}/preds_{stage_name}_{iteration}.json
        Output: {output_dir}/preds_{stage_name}_{iteration}_eval.json
        """
        preds_path = self.config.get_aug_preds_path(stage_name, iteration)
        if not preds_path.exists():
            self.logger.error(f"Aug preds not found: {preds_path}")
            return False

        run_id = f"{self.config.run_id}_{stage_name}_{iteration}"

        cmd = [
            "python", "-m", "swebench.runtest.run_evaluation_test_mutation_aug",
            "--predictions_test_path", str(preds_path),
            "--max_workers", str(self.config.eval_workers),
            "--timeout", "120",
            "--stage_name", stage_name,
            "--iteration", str(iteration),
            "--re_run_eval", "True",
            "--run_id", run_id,
            "--rewrite_preds", "True",
        ]

        self.logger.info(
            f"Running aug eval: stage={stage_name} iter={iteration}"
        )

        result = self._execute_command(
            cmd, f"aug_eval_{stage_name}_{iteration}", cwd=self.config.swe_bench_dir
        )
        return result.returncode == 0

    def _execute_command(
        self, cmd: List[str], phase: str, cwd: Path
    ) -> subprocess.CompletedProcess:
        """Execute command with real-time output using PTY for progress bar support"""
        self.logger.info(f"Executing: {' '.join(cmd)}")

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
                close_fds=True,
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
            self.logger.info(f"Log saved to: {log_file}")
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


class Stage3Orchestrator:
    """Main orchestrator for Stage 3 Augmented Test Pipeline"""

    def __init__(self, config: Stage3Config):
        self.config = config
        self.logger = self._setup_logger()
        self.tracker = AugTracker(config, self.logger)
        self.executor = AugExecutor(config, self.logger)
        self.report: Dict[str, Any] = {
            'run_id': config.run_id,
            'phases': {},
        }

    def run(self) -> bool:
        """Execute full Stage 3 Augmented Test Pipeline"""
        self.logger.info("=" * 80)
        self.logger.info("Starting Stage 3 Augmented Test Pipeline")
        self.logger.info("=" * 80)
        self.logger.info(f"Output directory:   {self.config.output_dir}")
        self.logger.info(f"Stage1 preds:       {self.config.stage1_preds_path}")
        self.logger.info(f"Stage2 output:      {self.config.stage2_output_dir}")
        self.logger.info(f"Model:              {self.config.model}")
        self.logger.info(f"Required mutations: {self.config.required_mutations}")
        self.logger.info(f"Max aug retries:    {self.config.max_aug_retries}")

        if self.config.instance_ids:
            self.logger.info(f"Instance IDs: {len(self.config.instance_ids)} specified")
        elif self.config.run_instance_file:
            self.logger.info(f"Instance file: {self.config.run_instance_file}")

        if self.config.start_from_phase:
            self.logger.info(f"Resuming from phase: {self.config.start_from_phase}")

        try:
            # Phase 1: Merge
            if should_run_phase("merge", self.config.start_from_phase):
                self.logger.info("\n" + "=" * 80)
                self.logger.info("PHASE 1: MERGE (Stage1 + Stage2 → pred_mutation.json)")
                self.logger.info("=" * 80)
                if not self._phase_merge():
                    self.logger.error("Phase 1 (merge) failed. Stopping pipeline.")
                    self._save_report()
                    return False
            else:
                self.logger.info("\nSkipping Phase 1: Merge")

            # Phase 2: Aug no-equivalent mutations
            if should_run_phase("aug_no_equ", self.config.start_from_phase):
                self.logger.info("\n" + "=" * 80)
                self.logger.info("PHASE 2: AUG NO-EQU (stage_name=no_equ_mutation_aug)")
                self.logger.info("=" * 80)
                if not self._phase_aug("aug_no_equ"):
                    self.logger.error("Phase 2 (aug_no_equ) failed. Stopping pipeline.")
                    self._save_report()
                    return False
            else:
                self.logger.info("\nSkipping Phase 2: Aug No-Equ")

            # Phase 3: Aug equivalent mutations
            if should_run_phase("aug_equ", self.config.start_from_phase):
                self.logger.info("\n" + "=" * 80)
                self.logger.info("PHASE 3: AUG EQU (stage_name=equ_mutation_aug)")
                self.logger.info("=" * 80)
                if not self._phase_aug("aug_equ"):
                    self.logger.error("Phase 3 (aug_equ) failed. Stopping pipeline.")
                    self._save_report()
                    return False
            else:
                self.logger.info("\nSkipping Phase 3: Aug Equ")

            self._save_report()
            return True

        except KeyboardInterrupt:
            self.logger.warning("\n\nInterrupted by user")
            self._save_report()
            return False
        except Exception as e:
            self.logger.error(f"Fatal error: {e}", exc_info=True)
            self._save_report()
            return False

    def _phase_merge(self) -> bool:
        """Phase 1: Merge Stage1 + Stage2 data"""
        pred_mutation_path = self.config.get_pred_mutation_path()

        if pred_mutation_path.exists():
            self.logger.info(f"pred_mutation.json already exists: {pred_mutation_path}")
            self.logger.info("Skipping merge (use --start-from-phase merge to redo)")
            self.report['phases']['merge'] = {'skipped': True, 'reason': 'already_exists'}
            return True

        success = self.executor.run_merge()
        self.report['phases']['merge'] = {'success': success}
        return success

    def _phase_aug(self, phase: str) -> bool:
        """
        Handle aug gen + eval + retry for all iterations of a given phase.
        phase: "aug_no_equ" or "aug_equ"
        """
        stage_cfg = AUG_STAGE_CONFIG[phase]
        stage_name = stage_cfg["stage_name"]
        num_iterations = self.config.required_mutations

        self.report['phases'][phase] = {'iterations': {}}

        for iteration in range(num_iterations):
            self.logger.info(f"\n{'=' * 60}")
            self.logger.info(f"  {stage_name} — Iteration {iteration}/{num_iterations - 1}")
            self.logger.info('=' * 60)

            # Determine input for this iteration
            if iteration == 0:
                aug_input = self.config.get_pred_mutation_path()
            else:
                aug_input = self.config.get_aug_eval_path(stage_name, iteration - 1)

            if not aug_input.exists():
                self.logger.warning(
                    f"Input for iteration {iteration} not found: {aug_input}. Skipping."
                )
                break

            success = self._run_aug_with_retries(stage_name, iteration, aug_input)
            iter_stats = self.tracker.get_aug_statistics(stage_name, iteration)
            self.report['phases'][phase]['iterations'][iteration] = iter_stats

            if not success and self.config.fail_fast:
                return False

            time.sleep(2)

        return True

    def _run_aug_with_retries(
        self,
        stage_name: str,
        iteration: int,
        aug_input: Path,
    ) -> bool:
        """
        Single iteration's aug gen → eval → retry loop.

        Resume logic:
        - If eval file already exists: check for failures, retry if needed
        - If preds file exists but no eval: run eval, then check
        - If neither: run gen, then eval, then check
        """
        preds_path = self.config.get_aug_preds_path(stage_name, iteration)
        eval_path = self.config.get_aug_eval_path(stage_name, iteration)

        if eval_path.exists():
            # Already have eval output — check for failures and retry from there
            self.logger.info(
                f"Eval file already exists for {stage_name} iter={iteration}, "
                f"checking for failures..."
            )
            still_needing = self.tracker.get_instances_needing_retry(stage_name, iteration)
            if not still_needing:
                self.logger.info(
                    f"  {stage_name} iter={iteration}: all instances successful (from cache)"
                )
                return True
            # Fall through to retry loop
            retry_start = 1
        elif preds_path.exists():
            # Have preds but no eval — run eval first
            self.logger.info(
                f"Preds exist for {stage_name} iter={iteration} but no eval. Running eval..."
            )
            self.executor.run_aug_eval(stage_name, iteration)
            still_needing = self.tracker.get_instances_needing_retry(stage_name, iteration)
            if not still_needing:
                return True
            retry_start = 1
        else:
            # Fresh start for this iteration
            retry_start = 0
            still_needing = None

        for retry in range(retry_start, self.config.max_aug_retries + 1):
            if retry == 0:
                # First run — no instance filter (or use config filter)
                self.logger.info(f"  Retry {retry}/{self.config.max_aug_retries}: running aug gen...")
                gen_ok = self.executor.run_aug_gen(stage_name, iteration, aug_input)
            else:
                # Retry — only for instances that still need it
                retry_ids = self.tracker.get_instances_needing_retry(stage_name, iteration)
                if not retry_ids:
                    self.logger.info(
                        f"  All instances successful after retry {retry - 1}"
                    )
                    return True
                self.logger.info(
                    f"  Retry {retry}/{self.config.max_aug_retries}: "
                    f"{len(retry_ids)} instances need retry"
                )
                for iid in retry_ids[:5]:
                    self.logger.info(f"    - {iid}")
                if len(retry_ids) > 5:
                    self.logger.info(f"    ... and {len(retry_ids) - 5} more")
                gen_ok = self.executor.run_aug_gen(stage_name, iteration, aug_input,
                                                   retry_ids=retry_ids)

            if not gen_ok:
                self.logger.warning(
                    f"  Aug gen failed (retry={retry}). Attempting eval anyway..."
                )

            eval_ok = self.executor.run_aug_eval(stage_name, iteration)
            if not eval_ok:
                self.logger.warning(f"  Aug eval failed (retry={retry})")

            time.sleep(1)

        # Final check
        final_needing = self.tracker.get_instances_needing_retry(stage_name, iteration)
        if final_needing:
            self.logger.warning(
                f"  {stage_name} iter={iteration}: {len(final_needing)} instances "
                f"still need aug after {self.config.max_aug_retries} retries. "
                f"Continuing pipeline."
            )
        else:
            self.logger.info(
                f"  {stage_name} iter={iteration}: all instances successful"
            )

        return True  # Don't block pipeline on retry exhaustion

    def _save_report(self):
        """Generate final report and save to JSON"""
        self.logger.info("\n" + "=" * 80)
        self.logger.info("STAGE 3 AUG PIPELINE FINAL REPORT")
        self.logger.info("=" * 80)

        # Print per-phase stats
        for phase in ["aug_no_equ", "aug_equ"]:
            phase_data = self.report['phases'].get(phase, {})
            iterations = phase_data.get('iterations', {})
            if not iterations:
                continue

            stage_name = AUG_STAGE_CONFIG[phase]["stage_name"]
            self.logger.info(f"\n{phase} ({stage_name}):")
            for iteration, stats in sorted(iterations.items()):
                if stats.get('exists'):
                    self.logger.info(
                        f"  iter={iteration}: total={stats['total']} "
                        f"success={stats['success']} "
                        f"no_patch={stats['no_patch']} "
                        f"not_caught={stats['still_not_caught']}"
                    )
                else:
                    self.logger.info(f"  iter={iteration}: no eval data")

        # Save to JSON
        report_path = self.config.output_dir / "stage3_aug_report.json"
        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(self.report, f, indent=2, ensure_ascii=False)
            self.logger.info(f"\nFull report saved to: {report_path}")
        except Exception as e:
            self.logger.error(f"Failed to save report: {e}")

        self.logger.info("=" * 80)

    def _setup_logger(self) -> logging.Logger:
        """Setup logger with file and console handlers"""
        logger = logging.getLogger("Stage3Aug")
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
        log_file = logs_dir / "stage3_aug.log"
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
        description="Stage 3 Aug Test Pipeline for SWE-PLUS",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ========== Required Arguments ==========
    parser.add_argument("--stage1-preds", type=str, required=True,
                        help="Path to Stage1 preds.json (used for merge step)")
    parser.add_argument("--stage2-output", type=str, required=True,
                        help="Stage2 base output dir (contains set1/, set2/, ...)")
    parser.add_argument("--model", "-m", type=str, required=True,
                        help="Model to use for aug test generation (e.g., openai/gpt-5)")

    # ========== Output Settings ==========
    parser.add_argument("--output", "-o", type=str, default="result/mutation_aug",
                        help="Base output directory (run written to {output}/{run_id}/)")
    parser.add_argument("--run-id", type=str, default="stage3_aug",
                        help="Run ID; output written to {output}/{run-id}/")

    # ========== Model Settings ==========
    parser.add_argument("--temperature", type=float, default=0.0,
                        help="Temperature for aug gen model")
    parser.add_argument("--aug-workers", type=int, default=2,
                        help="Parallel workers for aug generation")
    parser.add_argument("--eval-workers", type=int, default=8,
                        help="Parallel workers for evaluation")

    # ========== Dataset Settings ==========
    parser.add_argument("--benchmark", type=str, default="swebench",
                        choices=["swebench", "swebenchpro"],
                        help="Benchmark type")
    parser.add_argument("--subset", type=str, default="verified",
                        help="Dataset subset")
    parser.add_argument("--split", type=str, default="test",
                        help="Dataset split")

    # ========== Config Files ==========
    parser.add_argument("--aug-config", type=str,
                        default="./src/minisweagent/config/extra/swebench_aug_from_mutation.yaml",
                        help="Path to aug config file")

    # ========== Pipeline Settings ==========
    parser.add_argument("--required-mutations", type=int, default=2,
                        help="Number of aug iterations per stage (iterations 0..N-1)")
    parser.add_argument("--max-aug-retries", type=int, default=3,
                        help="Max retry loops per iteration")

    # ========== Instance Selection ==========
    parser.add_argument("--instance-ids", type=str, default=None,
                        help="Comma-separated instance IDs to process")
    parser.add_argument("--run-instance-file", type=str, default=None,
                        help="Path to YAML file with instance IDs")

    # ========== Behavior Flags ==========
    parser.add_argument("--fail-fast", action="store_true",
                        help="Stop pipeline on first failure")
    parser.add_argument("--start-from-phase", type=str,
                        choices=PIPELINE_PHASES,
                        default=None,
                        help="Resume pipeline from a specific phase")

    args = parser.parse_args()

    # Build paths
    output_dir = (Path(args.output) / args.run_id).resolve()
    mini_swe_agent_dir = Path(__file__).parent.resolve()
    swe_bench_dir = (mini_swe_agent_dir.parent / "swe-bench").resolve()

    # Parse instance IDs
    instance_ids_list = None
    if args.instance_ids:
        instance_ids_list = [
            iid.strip() for iid in args.instance_ids.split(",") if iid.strip()
        ]

    # Resolve config paths
    aug_config = Path(args.aug_config)
    if not aug_config.is_absolute():
        aug_config = (mini_swe_agent_dir / aug_config).resolve()

    # Build config
    config = Stage3Config(
        output_dir=output_dir,
        stage1_preds_path=Path(args.stage1_preds).resolve(),
        stage2_output_dir=Path(args.stage2_output).resolve(),

        mini_swe_agent_dir=mini_swe_agent_dir,
        swe_bench_dir=swe_bench_dir,

        model=args.model,
        temperature=args.temperature,
        aug_workers=args.aug_workers,
        eval_workers=args.eval_workers,

        benchmark=args.benchmark,
        subset=args.subset,
        split=args.split,

        aug_config_path=aug_config,

        required_mutations=args.required_mutations,
        max_aug_retries=args.max_aug_retries,

        run_id=args.run_id,
        fail_fast=args.fail_fast,
        start_from_phase=args.start_from_phase,

        instance_ids=instance_ids_list,
        run_instance_file=Path(args.run_instance_file).resolve()
        if args.run_instance_file else None,
    )

    # Validate required paths
    if not config.stage1_preds_path.exists():
        print(f"Error: Stage1 preds.json not found: {config.stage1_preds_path}")
        sys.exit(1)
    if not config.stage2_output_dir.exists():
        print(f"Error: Stage2 output dir not found: {config.stage2_output_dir}")
        sys.exit(1)
    if not swe_bench_dir.exists():
        print(f"Error: swe-bench directory not found: {swe_bench_dir}")
        sys.exit(1)
    if not aug_config.exists():
        print(f"Error: aug config not found: {aug_config}")
        sys.exit(1)

    # When resuming past merge, pred_mutation.json must exist
    if (args.start_from_phase and args.start_from_phase != "merge"
            and not config.get_pred_mutation_path().exists()):
        print(f"Error: pred_mutation.json not found: {config.get_pred_mutation_path()}")
        print(f"When resuming from '{args.start_from_phase}', merge must have already run.")
        sys.exit(1)

    # Run orchestrator
    orchestrator = Stage3Orchestrator(config)
    success = orchestrator.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
