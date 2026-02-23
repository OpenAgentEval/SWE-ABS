import os
import re
import subprocess
import tempfile

GIT_APPLY_CMDS = [
    "git apply --verbose",
    "git apply --verbose --reject",
    "patch --batch --fuzz=5 -p1 -i",
]


def get_apply_files(code_patch):
    apply_files = []
    for line in code_patch.splitlines(keepends=True):
        if line.startswith("diff --git"):
            apply_files.append(line.strip())

    return apply_files

# More robust diff header matching
DIFF_HEADER_PATTERN = r"^diff --git a/([^ \n]+) b/([^ \n]+)"

def prepare_directories_for_patch(env, patch_content: str, workdir: str = ""):
    """
    Parse local patch content and create directories inside the container via env.execute.
    Args:
        env: environment wrapper providing env.execute(cmd, cwd=workdir) to run commands in container
        patch_content: the diff text (string)
        workdir: working directory inside the container (cwd passed to env.execute)
    """
    new_files = []

    # Iterate over each line to find diff headers
    for line in patch_content.splitlines():
        m = re.match(DIFF_HEADER_PATTERN, line)
        if not m:
            continue
        old_path, new_path = m.groups()

        # New files in git diff appear as a/dev/null
        if old_path == "dev/null" or old_path == "/dev/null":
            new_files.append(new_path)

    # Create directories for each new file
    for nf in new_files:
        dirpath = os.path.dirname(nf)
        if not dirpath or dirpath == ".":
            continue
        # Use env.execute to create directories inside the container (consistent interface with other git_apply calls)
        try:
            mkdir_cmd = f"mkdir -p '{dirpath}'"
            res = env.execute(mkdir_cmd, cwd=workdir)
            # Optional: print or log res["output"] when debugging
            if res.get("returncode", 0) != 0:
                # Do not raise exception; log and continue (avoid blocking subsequent git apply attempts)
                print(f"[prepare_directories_for_patch] mkdir failed for {dirpath}: {res.get('output')}")
        except Exception as e:
            # Fault-tolerant: do not abort the entire flow on directory creation failure
            print(f"[prepare_directories_for_patch] exception while creating {dirpath}: {e}")


def git_apply(env, code_patch: str, patch_path: str = "patch.diff", workdir: str = ""):
    """
    Try to apply a patch inside the container using several strategies.

    Args:
        env: Environment wrapper with `execute` method.
        code_patch: The patch content as string.
        patch_path: Path (inside the container) to write the patch file.
        workdir: Git repo root inside container.

    Returns:
        list: list of applied files if success, otherwise empty list.
    """
    # 1. Write patch file into the container
    # Use docker cp to avoid argument list too long (OSError: Argument list too long)
    container_id = getattr(env, 'container_id', None)
    docker_executable = getattr(env.config, 'executable', 'docker') if hasattr(env, 'config') else 'docker'

    # Compute the full path inside the container
    if workdir:
        container_patch_path = f"{workdir.rstrip('/')}/{patch_path}"
    else:
        container_patch_path = patch_path

    try:
        # Write to a temp file, then copy into the container via docker cp
        with tempfile.NamedTemporaryFile(mode='w', suffix='.diff', delete=False) as tmp_file:
            tmp_file.write(code_patch)
            tmp_file_path = tmp_file.name

        try:
            # Use docker cp to copy the file into the container
            cp_cmd = [docker_executable, 'cp', tmp_file_path, f"{container_id}:{container_patch_path}"]
            cp_result = subprocess.run(cp_cmd, capture_output=True, text=True, timeout=30)
            if cp_result.returncode != 0:
                print(f"[git_apply] Failed to copy patch file to container: {cp_result.stderr}")
                return []
        finally:
            # Clean up temp file
            os.unlink(tmp_file_path)
    except Exception as e:
        print(f"[git_apply] Failed to write patch file via docker cp: {e}")
        return []

    # 2. Pre-create directories required for new files in the diff (parsed from local patch_content)
    # try:
    #     prepare_directories_for_patch(env, code_patch, workdir)
    # except Exception as e:
    # Directory creation failure should not be fatal (still attempt to apply patch), but log the error
    #     print(f"[git_apply] prepare_directories_for_patch failed (continuing): {e}")

    # 3. Try different apply commands in sequence
    for git_apply_cmd in GIT_APPLY_CMDS:
        full_cmd = f"{git_apply_cmd} {patch_path}"
        res = env.execute(full_cmd, cwd=workdir)

        if res.get("returncode", 0) == 0:
            print(f"[git_apply] Patch applied successfully with `{git_apply_cmd}`")
            apply_files = get_apply_files(code_patch)
            env.execute(f"rm -f {patch_path}", cwd=workdir)
            return apply_files
        else:
            print(f"[git_apply] Failed with `{git_apply_cmd}`:\n{res.get('output')}")

    print("[git_apply] All patch commands failed.")
    # 4. Clean up patch file
    env.execute(f"rm -f {patch_path}", cwd=workdir)
    return []






# if __name__ == "__main__":

#     from pathlib import Path

#     traj_path = Path("/home/ddq/CaoYang/SWE-PLUS/mini-swe-agent/result/model_gen_test/pro_0_100/traj/gen_0")
#     instance_id = 'instance_gravitational__teleport-c782838c3a174fdff80cafd8cd3b1aa4dae8beb2'


#     traj_file = 
#     with open("")