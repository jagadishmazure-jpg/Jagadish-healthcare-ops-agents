"""Print the real test count on stdout (never swallowed by -qq)."""
import subprocess
import sys

out = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/", "-q"],
    capture_output=True, text=True, cwd=".",
)
lines = [line for line in out.stdout.strip().splitlines() if line.strip()]
print(lines[-1] if lines else "no test output")
sys.exit(out.returncode)
