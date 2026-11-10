"""Copy the model and search keys the cloud runner needs into its Vercel project, and into this machine's .env.

    .venv/bin/python scripts/push_keys.py [path to the old brief builder's .env]

Reads ANTHROPIC_API_KEY from ./.env and the brief builder's settings from its old .env (default
~/Desktop/projects/shoals-new/.env). Each value goes to `vercel env add` on standard input for the
project linked in this folder (swarmz-engine), marked sensitive; nothing is printed but the names.
The builder's settings are also added to ./.env as BRIEF_<name>, where swarmz now looks for them.
"""

import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
BUILDER = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL_NAME", "ASSEMBLER_MODEL", "ASSEMBLER_BASE_URL",
           "ASSEMBLER_API_KEY", "SCRUBBER_MODEL", "SCRUBBER_BASE_URL", "SCRUBBER_API_KEY", "BRAVE_API_KEY")


def main() -> int:
    old = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Desktop/projects/shoals-new/.env"
    mine, theirs = dotenv_values(ROOT / ".env"), dotenv_values(old) if old.exists() else {}
    if not old.exists():
        print(f"not found: {old}")
    values = {"ANTHROPIC_API_KEY": mine.get("ANTHROPIC_API_KEY")}
    values.update({"BRIEF_" + k: mine.get("BRIEF_" + k) or theirs.get(k) for k in BUILDER})
    missing = [k for k, v in values.items() if not v]
    for name, value in values.items():
        if not value:
            continue
        done = subprocess.run(["vercel", "env", "add", name, "production", "--sensitive", "--force"],
                              cwd=ROOT, input=value, text=True, capture_output=True)
        print(f"{'set    ' if done.returncode == 0 else 'FAILED '} {name}" + ("" if done.returncode == 0 else f": {done.stderr.strip()[-200:]}"))
    add = [f"{k}={v}" for k, v in values.items() if k.startswith("BRIEF_") and v and not mine.get(k)]
    if add:
        with open(ROOT / ".env", "a") as f:
            f.write("\n# ---- brief builder (added by scripts/push_keys.py) ----\n" + "\n".join(add) + "\n")
        print(f"added {len(add)} BRIEF_ settings to .env")
    if missing:
        print("no value found for: " + ", ".join(missing))
    print("Redeploy for them to take effect: vercel deploy --prod")
    return 0


if __name__ == "__main__":
    sys.exit(main())
