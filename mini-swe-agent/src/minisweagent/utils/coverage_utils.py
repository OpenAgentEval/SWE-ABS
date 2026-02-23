import ast
from collections import defaultdict
import copy
import json
import re
from pathlib import Path
from unidiff import PatchSet
from typing import List, Dict, Set, Tuple, Union, Iterable
from minisweagent.utils.constants import (
    START_TEST_OUTPUT,
    END_TEST_OUTPUT,
)



def parse_trace_log(output_path: str):
    if not Path(output_path).exists():
        return {}
    with open(output_path, "r") as f:
        eval_output = f.readlines()

    coverage = {}

    for i, line in enumerate(eval_output):
        if line.strip() == "+ cat coverage.cover":
            break
    for line in eval_output[i+1:]:
        if not line.startswith("{\"/testbed"):
            continue

        try:
            d = json.loads(line.strip())
            for file_name, file_coverage in d.items():
                key = file_name.replace("/testbed/", "")
                exe_lines = set()
                if key in coverage:
                    exe_lines = coverage[key]["executed_lines"]
                for line_id, line_coverage in file_coverage.items():
                    if line_coverage>0:
                        exe_lines.add(int(line_id))
                
                coverage[key] = {"executed_lines": exe_lines}
        except json.JSONDecodeError:
            continue
    return coverage 


def compute_coverage(output_path, modified_related_lines, use_key = "exe_slice_lines_scope"):
    
    if len(modified_related_lines) == 0:
        return 1, {}
    
    trace_coverage = parse_trace_log(output_path)

    if len(trace_coverage) == 0:
        return 404, {}

    total_avg = 0
    un_hit_lines_content = defaultdict(list)
    for file_name in modified_related_lines:
        lines = set(modified_related_lines[file_name][use_key])
        if len(lines) == 0:
            continue
        trace_exe_lines = set(trace_coverage.get(file_name, {}).get('executed_lines', set()))
        un_hit_lines = lines - trace_exe_lines
        if len(un_hit_lines) == 0:
            total_avg += 1
            continue
        total_avg += (1 - len(un_hit_lines) / len(lines))
        content = modified_related_lines[file_name]["content"].split("\n")
        # Extract unexecuted lines
        for line in sorted(list(un_hit_lines)):
            un_hit_lines_content[file_name].append((line,content[line-1]))
    total_avg /= len(modified_related_lines)
    if len(un_hit_lines_content) == 0:
        return 1.0, {}

    return round(total_avg, 3), dict(un_hit_lines_content)



def get_error_info(aug_test_log_file:Path):
    content = aug_test_log_file.read_text()
    test_content = content.split(START_TEST_OUTPUT)[1].split(END_TEST_OUTPUT)[0]
    return test_content