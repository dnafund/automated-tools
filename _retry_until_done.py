"""Loop: run signup repeatedly until 0 pending accounts (H + M both done)."""
import json
import subprocess
import sys
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).parent
ACCOUNTS_FILE = TOOLS_DIR / "accounts.json"


def count_pending():
    accounts = json.load(open(ACCOUNTS_FILE, encoding="utf-8"))
    h_pending = sum(1 for a in accounts if not a.get("helius_signed_up"))
    m_pending = sum(1 for a in accounts if not a.get("moralis_signed_up"))
    return len(accounts), h_pending, m_pending


def main():
    max_rounds = 20  # safety cap
    for round_num in range(1, max_rounds + 1):
        total, h_pend, m_pend = count_pending()
        print(f"\n{'='*60}")
        print(f"ROUND {round_num}: total={total} | H pending={h_pend} | M pending={m_pend}")
        print(f"{'='*60}")

        if h_pend == 0 and m_pend == 0:
            print(f"🏁 ALL DONE after {round_num - 1} rounds!")
            return

        # Run signup (processes pending accounts only)
        env = {"PYTHONIOENCODING": "utf-8"}
        import os
        env = {**os.environ, **env}
        print(f"Starting signup round {round_num}...")
        result = subprocess.run(
            [sys.executable, "dual_farmer.py", "signup"],
            cwd=str(TOOLS_DIR),
            env=env,
        )
        print(f"Round {round_num} done (exit {result.returncode})")

        # Quick cool-off before next round
        time.sleep(30)

    total, h_pend, m_pend = count_pending()
    print(f"\n⚠️ Reached max rounds ({max_rounds}). Still pending: H={h_pend} M={m_pend}")


if __name__ == "__main__":
    main()
