#!/usr/bin/env python3
"""
Wrapper Generator
=================
Generates a Python module `bio_wrappers.py` containing helper functions
for each documented tool. These wrappers abstract the Docker command construction.
"""

import os
import re
import glob
from pathlib import Path

# Base directory relative to this script
SCRIPT_DIR = Path(__file__).resolve().parent
BIO_TOOLS_DIR = SCRIPT_DIR.parent

GUIDE_DIR = BIO_TOOLS_DIR / "Guide"
OUTPUT_FILE = SCRIPT_DIR / "bio_wrappers.py"

TEMPLATE_HEADER = '''"""
Bioinformatics Tool Wrappers
============================
Auto-generated wrappers for Docker-based bioinformatics tools.
"""

import subprocess
import os
from pathlib import Path
from typing import List, Optional, Union

def _run_docker_cmd(image: str, cmd_args: List[str], mounts: List[str] = [], envs: List[str] = []):
    """Internal helper to run docker commands."""
    
    # Base command
    docker_cmd = ["docker", "run", "--rm"]
    
    # Add mounts (vols)
    for mount in mounts:
        docker_cmd.extend(["-v", mount])
        
    # Add envs
    for env in envs:
        docker_cmd.extend(["-e", env])
        
    # Add image
    docker_cmd.append(image)
    
    # Add tool arguments
    docker_cmd.extend(cmd_args)
    
    print(f"Executing: {' '.join(docker_cmd)}")
    
    try:
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {e.stderr}")
        raise e

'''

def parse_tools(directory):
    tools = []
    for filepath in glob.glob(str(directory / "**/*.md"), recursive=True):
        if filepath.endswith("README.md") or filepath.endswith("TEMPLATE.md") or filepath.endswith("bio_tools_help.md"):
            continue
            
        with open(filepath, 'r') as f:
            content = f.read()
            
        tools.append({
            "name": extract_name(content),
            "image": extract_docker_image(content),
            "filename": Path(filepath).stem
        })
    return tools

def extract_name(content):
    match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    return match.group(1).strip() if match else "Unknown"

def extract_docker_image(content):
    match = re.search(r"\*\*Docker Image\*\*:\s*`([^`]+)`", content)
    return match.group(1).strip() if match else None

def generate_wrapper_code(tool):
    """Generates a python function for the tool."""
    # Remove special chars: keeping only alphanumeric and underscores
    # First replace spaces and hyphens with underscores
    name_clean = tool['name'].replace(" ", "_").replace("-", "_").lower()
    # Remove any other non-identifier chars (parentheses, !, +)
    name_clean = re.sub(r'[^a-z0-9_]', '', name_clean)
    # merged underscores
    name_clean = re.sub(r'_+', '_', name_clean).strip('_')
    if tool['name'] == "Unknown" or not tool['image']:
        return ""
        
    func_name = f"run_{name_clean}"
    
    code = f'''
def {func_name}(args: List[str], data_dir: str = "/data"):
    """
    Run {tool['name']} using {tool['image']}.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "{tool['image']}"
    mounts = [f"{{os.path.abspath(data_dir)}}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)
'''
    return code

def main():
    tools = parse_tools(GUIDE_DIR)
    
    with open(OUTPUT_FILE, 'w') as f:
        f.write(TEMPLATE_HEADER)
        
        for tool in tools:
            wrapper = generate_wrapper_code(tool)
            f.write(wrapper)
            
    print(f"Generated wrappers for {len(tools)} tools in {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
