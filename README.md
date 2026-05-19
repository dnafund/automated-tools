# Automated Tools

Collection of browser automation tools built with Playwright for bulk-farming free-tier API keys.

Currently supported providers:

- **[Helius](https://helius.dev)** — Solana RPC + Enhanced API (1M credits / 10 rps free tier)
- **[Moralis](https://moralis.com)** — Web3 multichain data API (free tier)

Both farmers share the same Gmail account pool (bought from [mail72h.com](https://mail72h.com)) and the same Google OAuth signup flow. The `dual_farmer.py` script signs up the same account on **both** providers concurrently in the same run.

---

## Prerequisites

- Python 3.10+
- [mail72h.com](https://mail72h.com) account with balance (only required if you want to buy Gmail accounts via the tool — you can also add accounts manually)

## Installation

```bash
git clone https://github.com/dnafund/automated-tools.git
cd automated-tools

pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Edit .env — fill in MAIL72H_USER and MAIL72H_PASS (skip if adding accounts manually)
```

## Configuration

| Variable | Description |
|---|---|
| `MAIL72H_USER` | mail72h.com username (only needed for `buy` / `auto` modes) |
| `MAIL72H_PASS` | mail72h.com password |

---

## Helius API Key Farmer

```bash
# Full auto: buy N Gmail accounts → signup → extract keys
python3 helius_key_farmer.py auto --count 10

# Buy Gmail accounts only
python3 helius_key_farmer.py buy --count 10

# Signup all pending accounts (skip if helius_signed_up = true)
python3 helius_key_farmer.py signup

# Show collected API keys
python3 helius_key_farmer.py keys

# Manually paste accounts (email|password, one per line)
python3 helius_key_farmer.py add
```

Output: `helius_keys.json` + `HELIUS_API_KEYS=k1,k2,k3,...` appended to `.env`.

## Moralis API Key Farmer

```bash
# Signup all pending accounts on Moralis (uses same accounts.json)
python3 moralis_key_farmer.py signup

# Show collected keys
python3 moralis_key_farmer.py keys

# Add accounts manually
python3 moralis_key_farmer.py add
```

Output: `moralis_keys.json` + `MORALIS_API_KEYS=k1,k2,k3,...` appended to `.env`.

The Moralis flow handles the onboarding form (role, company, source, telegram), selects the Free plan, dismisses the payment-card prompt, and extracts the API key from the dashboard (clipboard interception + DOM fallback).

## Dual Farmer (Helius + Moralis in one run)

```bash
# Bulk-paste accounts (email|password)
python3 dual_farmer.py add

# Run both signups for every pending account
python3 dual_farmer.py signup

# Sequential mode — safer if Google flags concurrent OAuth
python3 dual_farmer.py signup --seq

# Status / retry
python3 dual_farmer.py status
python3 dual_farmer.py retry
```

For each account, Helius and Moralis signups run in two separate browser contexts inside the same Playwright instance.

---

## Auxiliary Scripts (`_*`)

| Script | Purpose |
|---|---|
| `_helius_login_retry.py` | Retry key extraction via `/login` for accounts already signed up on Helius but where key extraction failed. Sequential, parallel-safe with the Moralis retry. |
| `_moralis_login_retry.py` | Same as above for Moralis. |
| `_retry_until_done.py` | Wrapper loop — re-runs both signups until `helius_pending == 0` and `moralis_pending == 0` (safety cap: 20 rounds). |
| `_reset_moralis.py` | Reset `moralis_signed_up` flag on all accounts (use when you want to re-run Moralis signup for everyone). Also imports new accounts from `pending_accounts.txt`. |
| `_run_helius_farm.bat` | Windows scheduled-task launcher — runs `helius_key_farmer.py signup -w 5` and logs to `helius_farm.log`. |
| `_run_moralis_farm.bat` | Same for Moralis. |

## File Structure

```
automated-tools/
  helius_key_farmer.py        # Helius automation
  moralis_key_farmer.py       # Moralis automation
  dual_farmer.py              # Run both providers concurrently
  _helius_login_retry.py      # Retry Helius key extraction
  _moralis_login_retry.py     # Retry Moralis key extraction
  _retry_until_done.py        # Loop until all pending = 0
  _reset_moralis.py           # Reset Moralis flag + import new accounts
  _run_helius_farm.bat        # Windows scheduled-task launcher
  _run_moralis_farm.bat       # Same for Moralis
  requirements.txt
  .env.example
  # Git-ignored runtime artifacts:
  .env                        # Your credentials
  accounts.json               # Gmail accounts [{email, password, recovery, *_signed_up}]
  helius_keys.json            # Extracted Helius keys
  moralis_keys.json           # Extracted Moralis keys
  pending_accounts.txt        # Plain-text email|password list to import
  *.log                       # Run logs
```

## How It Works

1. **Anti-detection** — randomized viewport, spoofed user-agent, human-like typing delays, `playwright-stealth` JS patches.
2. **Google OAuth** — handles email → password → speedbump/TOS → consent screen for every fresh account.
3. **Provider onboarding** — Helius selects Free plan + creates project; Moralis fills the onboarding questionnaire + selects Free plan + dismisses card prompt.
4. **Key extraction** — intercepts clipboard `writeText`, falls back to DOM parsing + regex.
5. **Auto `.env` update** — appends keys to `HELIUS_API_KEYS=` / `MORALIS_API_KEYS=` (comma-separated).

## Notes

- Runs in **headed mode** (visible browser) by default for reliability.
- Each account uses its own browser context (isolated cookies/sessions).
- If auto-extraction fails, the script falls back to manual paste.
- Free tiers: Helius 1M credits / 10 rps; Moralis 40k CU/day.

---

## Troubleshooting

### Setup / install

**`playwright._impl._errors.Error: Executable doesn't exist at .../chrome-win/chrome.exe`**
Chromium binary missing. Run:
```bash
playwright install chromium
```

**`ModuleNotFoundError: No module named 'playwright_stealth'`**
```bash
pip install -r requirements.txt
```

**Windows: garbled output / `UnicodeEncodeError: 'charmap'`**
The `.bat` launchers already set `PYTHONUTF8=1`. If running manually:
```powershell
$env:PYTHONUTF8=1
python -X utf8 helius_key_farmer.py signup
```

### Google OAuth

**`Couldn't sign you in` / `This browser or app may not be secure`**
Google flagged the automation. Fixes (try in order):
1. Slow down — run `dual_farmer.py signup --seq` instead of concurrent.
2. Make sure you didn't recently sign in to that Gmail from another IP (use the account once manually first to clear the new-device prompt).
3. Wait 30–60 min before retrying that account (Google has a short cooldown).
4. If the account is permanently flagged → mark it failed in `accounts.json` and buy a fresh one.

**Speedbump page (`Confirm it's you` / phone challenge)**
The script clicks "Try another way" automatically. If it stalls, the page layout changed — open the failed account manually, complete the speedbump once, then re-run the retry script.

**Captcha shown**
Solve it manually in the visible browser window — the script waits for navigation and resumes. If captchas appear on every run, you're going too fast: lower concurrency to `-w 1` or `-w 2`.

### Helius

**Stuck at `Get Started` button**
The dashboard sometimes A/B-tests the onboarding. The script tries multiple selectors; if it still fails, complete that one signup manually, set `helius_signed_up: true` in `accounts.json`, and re-run `signup`.

**Key extraction failed → empty `helius_keys.json` entry**
Run:
```bash
python3 _helius_login_retry.py
```
Logs in via `/login` and re-extracts. If still failing, the clipboard permission was denied — open the dashboard manually for that account, copy the key, then add it via `python3 helius_key_farmer.py keys` and edit `helius_keys.json` directly.

### Moralis

**Onboarding form field not found** (`role`, `company`, `where_heard`, `telegram`)
Moralis updates the form labels occasionally. Open `moralis_key_farmer.py` and search for the field's `data-testid` / placeholder string — update to match what you see in DevTools. The other steps (Free plan, payment skip) should still work.

**`Add Payment Option` page won't dismiss**
The "Add Card Later" button moved. Click it manually once — the script will continue after the dashboard loads.

**Key shown as `•••••••` and never reveals**
Clipboard interception missed. Run:
```bash
python3 _moralis_login_retry.py
```
which logs in fresh and intercepts the copy event again.

### Concurrency / rate-limit

**Google blocks after a few accounts**
Drop to `-w 1` (workers = 1) or use `--seq` in `dual_farmer.py`. The defaults assume a fresh residential IP; on cloud/VPS IPs you'll get flagged faster.

**`429 Too Many Requests` from Helius / Moralis dashboard**
You're spamming the same IP. Wait 10–15 min and resume — the retry scripts pick up where they left off (idempotent on `*_signed_up` flag).

### Data files

**`accounts.json` got corrupted (truncated / mid-write crash)**
Check `accounts.json.bak` (the farmers write a backup before each save). Restore it:
```bash
cp accounts.json.bak accounts.json
```

**`pending_accounts.txt` import isn't picking up new accounts**
Format must be one `email|password` per line. Use `_reset_moralis.py` to re-import — it dedupes against existing emails.

### Logs

Every signup run writes to `helius_farm.log` / `moralis_farm.log`. Search for `ERROR` / `Exception`:
```bash
grep -i "error\|exception" helius_farm.log | tail -20
```
Retry scripts write to `helius_retry.log` / `moralis_retry.log`.

---

## Disclaimer

For educational and personal use. Respect the ToS of every platform involved. The maintainer is not responsible for misuse.
