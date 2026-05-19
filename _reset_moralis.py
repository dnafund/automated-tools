"""Reset moralis_signed_up flag + import 500 new accounts from pending_accounts.txt"""
import json
from pathlib import Path
from datetime import datetime, timezone

tools_dir = Path(__file__).parent
accounts_file = tools_dir / "accounts.json"
pending_file = tools_dir / "pending_accounts.txt"

# Load accounts
accounts = json.load(open(accounts_file, encoding="utf-8")) if accounts_file.exists() else []
existing_emails = {a["email"] for a in accounts}

# Reset moralis flag on ALL existing accounts
reset_count = 0
for a in accounts:
    if a.get("moralis_signed_up"):
        a["moralis_signed_up"] = False
        reset_count += 1

# Import pending new accounts
added = 0
dupes = 0
if pending_file.exists():
    for line in open(pending_file, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 2 and "@" in parts[0]:
            email = parts[0]
            if email in existing_emails:
                dupes += 1
                continue
            accounts.append({
                "email": email,
                "password": parts[1],
                "recovery": parts[2] if len(parts) > 2 else "",
                "bought_at": datetime.now(timezone.utc).isoformat(),
                "helius_signed_up": False,
                "moralis_signed_up": False,
            })
            existing_emails.add(email)
            added += 1

json.dump(accounts, open(accounts_file, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

print(f"Reset moralis_signed_up: {reset_count} accounts")
print(f"New accounts added: {added} | Duplicates skipped: {dupes}")
print(f"Total accounts now: {len(accounts)}")
