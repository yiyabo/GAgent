#!/usr/bin/env python3
"""
Auto-Test Tools Script
======================
This script parses bioinformatics tool documentation in `tool_box/bio_tools/Guide/`,
extracts Docker commands, and executes them against dummy test data to verify functionality.

It generates a report in `tool_box/bio_tools/test_log.md`.
"""

import os
import re
import subprocess
import glob
from pathlib import Path
from datetime import datetime

# Configuration
# Base directory relative to this script (assuming script is in tool_box/bio_tools/scripts/)
SCRIPT_DIR = Path(__file__).resolve().parent
BIO_TOOLS_DIR = SCRIPT_DIR.parent
GUIDE_DIR = BIO_TOOLS_DIR / "Guide"
TEST_DATA_DIR = BIO_TOOLS_DIR / "test_data"
LOG_FILE = BIO_TOOLS_DIR / "test_log.md"

# Mappings for test data substitution
# We replace common doc placeholders with our actual test files
PATH_MAPPINGS = {
    r"/data/input\.fasta": "/data/test.fasta",
    r"/data/sequences\.fasta": "/data/test.fasta",
    r"/data/input\.fastq": "/data/test.fastq",
    r"/data/reads\.fastq": "/data/test.fastq",
    r"/data/user_data": "/data",  # Map user_data mount to /data
    r"/path/to/data": "/data"     # Map generic path to /data
}

def parse_markdown_files(directory):
    """Recursively find and parse .md files."""
    tools = []
    for filepath in glob.glob(str(directory / "**/*.md"), recursive=True):
        if filepath.endswith("README.md") or filepath.endswith("TEMPLATE.md") or filepath.endswith("bio_tools_help.md"):
            continue
            
        with open(filepath, 'r') as f:
            content = f.read()
            
        tool_info = {
            "file": filepath,
            "name": extract_name(content),
            "image": extract_docker_image(content),
            "examples": extract_agent_examples(content)
        }
        
        if tool_info["image"]:
            tools.append(tool_info)
            
    return tools

def extract_name(content):
    match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    return match.group(1).strip() if match else "Unknown"

def extract_docker_image(content):
    match = re.search(r"\*\*Docker Image\*\*:\s*`([^`]+)`", content)
    return match.group(1).strip() if match else None

def extract_agent_examples(content):
    """Extract command blocks from 'Examples for Agent' section."""
    examples = []
    # Find the section
    section_match = re.search(r"## Examples for Agent(.*?)(?=^## |\Z)", content, re.DOTALL | re.MULTILINE)
    if not section_match:
        return examples
        
    section_content = section_match.group(1)
    
    # Extract code blocks
    code_blocks = re.findall(r"```bash\n(.*?)\n```", section_content, re.DOTALL)
    for block in code_blocks:
        cmd = block.strip()
        # Basic cleanup: remove backslashes for multi-line commands to make them single line for easier analysis (optional)
        # But for execution, we might want to keep them or clean them up. 
        # For now, let's keep them as is but ensure we handle line continuations.
        examples.append(cmd)
        
    return examples

def prepare_command(cmd, test_data_path):
    """
    Prepare the command for execution:
    1. Replace mount paths with our test data path.
    2. Replace input file placeholders with valid test files.
    """
    # 1. Normalize mounts: ensure we mount our TEST_DATA_DIR to /data
    # We look for `-v ...:...` patterns and ensure we are mounting our local dir
    
    # Strategy: 
    # Instead of complex regex replacement of the mount point which might vary,
    # let's Construct a CLEAN command.
    # We strip the original `docker run ... image ...` part and rebuild it with our standardized mount.
    
    # HOWEVER, the doc commands often contain specific flags.
    # Easier approach: String substitution on the mapped paths.
    
    modified_cmd = cmd
    
    # Replace volume mounts first
    # Replace /data/user_data with our local absolute path
    modified_cmd = modified_cmd.replace("/data/user_data", str(test_data_path))
    modified_cmd = modified_cmd.replace("/path/to/data", str(test_data_path))
    
    # Also replace just "-v /data:/data" type things if they exist
    # But usually the doc says `-v /data/user_data:/data`. 
    
    # Pre-create likely output directories in test_data_path
    # Many tools fail if output subdir doesn't exist
    for subdir in ["output", "bins", "results", "analysis", "qc", "annotation", "taxonomy"]:
        (test_data_path / subdir).mkdir(exist_ok=True)
        
    # Heuristic: If command outputs to a file in a subdir (e.g., /data/bins/bin), ensure 'bins' exists
    # We already did general creation, but let's check command specific paths if needed.

    # If the user didn't use the standard mount path in the doc, this heuristic might fail.
    # Let's try aggressive replacement of input files first.
    
    for pattern, replacement in PATH_MAPPINGS.items():
        # Only replace if relevant input pattern
         modified_cmd = re.sub(pattern, replacement, modified_cmd)

    # Now handle the mount. We want to ensure the container sees /data mounted from our TEST_DATA_DIR
    # The doc command usually looks like: `docker run ... -v /host/path:/container/path ...`
    # We want to force `-v LOCAL_TEST_DIR:/data` and ensure the command uses `/data/test.fasta`
    
    # Blind substitution of the mount part:
    # We generally assume the doc mounts to `/data`.
    # So we want to replace whatever comes before `:/data` with `TEST_DATA_DIR`.
    
    # Matches -v /anything/here:/data
    modified_cmd = re.sub(r"-v\s+\S+:/data", f"-v {test_data_path}:/data", modified_cmd)
    
    return modified_cmd

def run_command(cmd, timeout=60):
    """Run shell command and return result."""
    try:
        # Use shell=True to handle pipes and complex args
        result = subprocess.run(
            cmd, 
            shell=True, 
            capture_output=True, 
            text=True, 
            timeout=timeout
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[:1000], 
            "stderr": result.stderr[:1000],
            "cmd": cmd,
            "returncode": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": "Command timed out",
            "cmd": cmd,
            "returncode": -1
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "cmd": cmd,
            "returncode": -1
        }

def attempt_fix(cmd, result, test_data_path):
    """
    Attempt to fix common errors and return a new command (or None if no fix found).
    """
    stderr = result['stderr'].lower()
    new_cmd = cmd
    fixed = False
    
    # Fix 1: Output directory does not exist
    # Heuristic: verify if the command has an output flag like -o /data/output/file
    # and create that directory.
    # Since we mapped /data to test_data_path, we check if we need to make subdirs.
    # But for Docker, the volume is already mounted. 
    # If the tool complains "Cannot create file... directory missing", we might need to create it locally.
    
    # Fix 2: "Permission denied"
    # Try adding user mapping if image supports it, or just note it.
    # docker run -u $(id -u):$(id -g) ...
    if "permission denied" in stderr and "-u " not in new_cmd:
        # Only works if we can calculate uid/gid (linux/mac)
        try:
            uid = os.getuid()
            gid = os.getgid()
            # Insert -u before the image name? Hard to parse safely.
            # Insert after "run "
            new_cmd = new_cmd.replace("docker run ", f"docker run -u {uid}:{gid} ")
            fixed = True
        except:
            pass

    # Fix 3: Input file missing (maybe the mapping failed?)
    # If error says "File not found", check if we can switch to a generic test file
    if "no such file" in stderr or "file not found" in stderr:
        # Check if we missed mapping a file extension
        pass

    return new_cmd if fixed else None

def main():
    print(f"Starting Auto-Test on {GUIDE_DIR}...")
    tools = parse_markdown_files(GUIDE_DIR)
    
    results_log = [
        "# Bioinformatics Tools Auto-Test Log",
        f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "| Tool | Image | Sanity Check | Example 1 | Status | Notes |",
        "|---|---|---|---|---|---|"
    ]
    
    for tool in tools:
        print(f"Testing {tool['name']} ({tool['image']})...")
        
        # 1. Sanity Check (Version/Help)
        sanity_cmd = f"docker run --rm {tool['image']} --help"
        if "seqkit" in tool['name'].lower():
             sanity_cmd = f"docker run --rm {tool['image']} version"
             
        sanity_res = run_command(sanity_cmd)
        sanity_status = "✅" if sanity_res['success'] else "❌"
        
        # 2. Run first example
        ex_status = "Skipped"
        notes = ""
        
        if tool['examples']:
            # Use the first example
            raw_cmd = tool['examples'][0]
            patched_cmd = prepare_command(raw_cmd, TEST_DATA_DIR)
            
            # Ensure output directory exists (heuristic)
            # If the command outputs to /data/output.fasta, and /data is mapped to TEST_DATA_DIR,
            # then TEST_DATA_DIR is existing. But if it outputs to /data/subdir/output.fasta...
            # We don't easily know.
            
            ex_res = run_command(patched_cmd)
            
            # Auto-Fix Retry Logic
            if not ex_res['success']:
                print(f"  [Fail] {tool['name']} example failed. Attempting fix...")
                
                # Check for registry/network issues first to avoid futile retries
                if "denied" in ex_res['stderr'] and "pull access" in ex_res['stderr']:
                     # Registry error, probably need docker login or image is private/gone
                     notes = f"<details><summary>Error (Registry)</summary>Registry access denied. Check defaults or docker login.<br>Original: {ex_res['stderr']}</details>"
                
                # Check for timeout
                elif "Command timed out" in ex_res['stderr']:
                     # Try extending timeout once
                     print(f"  [Fix] Command timed out. Retrying with longer timeout (300s)...")
                     retry_res = run_command(patched_cmd, timeout=300)
                     if retry_res['success']:
                        ex_res = retry_res
                        notes = "⚠️ Fixed by Agent (Extended Timeout)"
                     else:
                        notes = f"<details><summary>Error (Timeout)</summary>Command timed out even after extension.<br>Original: {ex_res['stderr']}</details>"
                
                else:
                    # Try standard heuristics
                    fixed_cmd = attempt_fix(patched_cmd, ex_res, TEST_DATA_DIR)
                    if fixed_cmd and fixed_cmd != patched_cmd:
                        print(f"  [Fix] Retrying with modified command...")
                        print(f"  [Debug] New Cmd: {fixed_cmd}")
                        retry_res = run_command(fixed_cmd, timeout=120)
                        if retry_res['success']:
                            ex_res = retry_res
                            notes = "⚠️ Fixed by Agent (Auto-correction applied)"
                        else:
                             notes = f"<details><summary>Error (Fix Failed)</summary>Original: {ex_res['stderr']}<br>Retry: {retry_res['stderr']}</details>"
                    else:
                        notes = f"<details><summary>Error</summary>Code: {ex_res['stderr']}</details>"
            
            ex_status = "✅" if ex_res['success'] else "❌"
        else:
            ex_status = "No Ex"
            
        overall = "Pass" if sanity_res['success'] and (ex_status == "✅" or ex_status == "Skipped") else "Fail"
        if ex_status == "❌": overall = "Partial"
        
        # Table Row
        row = f"| [{tool['name']}]({str(tool['file'])}) | `{tool['image']}` | {sanity_status} | {ex_status} | {overall} | {notes} |"
        results_log.append(row)
        
    # Write Log
    with open(LOG_FILE, 'w') as f:
        f.write("\n".join(results_log))
        
    print(f"Done! Log written to {LOG_FILE}")

if __name__ == "__main__":
    main()
