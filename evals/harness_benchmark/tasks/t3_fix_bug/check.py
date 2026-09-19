import subprocess, sys
r = subprocess.run([sys.executable, "test_check.py"], capture_output=True, text=True)
assert r.returncode == 0, r.stdout + r.stderr
