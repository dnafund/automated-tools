# Automated Tools

Collection of browser automation tools built with Playwright.

## Helius API Key Farmer

Automates bulk creation of [Helius](https://helius.dev) API keys via Google OAuth signup.

**Pipeline:**
1. Buy Gmail accounts from [mail72h.com](https://mail72h.com)
2. Sign up on Helius using Google OAuth for each account
3. Extract API keys and save to `.env`

Useful for projects that need multiple Helius API keys for rate-limit rotation.

---

## Prerequisites

- Python 3.10+
- [mail72h.com](https://mail72h.com) account with balance

## Installation

```bash
# Clone
git clone https://github.com/dnafund/automated-tools.git
cd automated-tools

# Install dependencies
pip install -r requirements.txt

# Install Chromium for Playwright
playwright install chromium

# Configure credentials
cp .env.example .env
nano .env   # fill in MAIL72H_USER and MAIL72H_PASS
```

## Configuration

Copy `.env.example` to `.env` and fill in your values:

| Variable | Description |
|---|---|
| `MAIL72H_USER` | Your mail72h.com username |
| `MAIL72H_PASS` | Your mail72h.com password |

## Usage

### Full auto pipeline (buy + signup + extract)

```bash
python3 helius_key_farmer.py auto --count 10
```

Buys 10 Gmail accounts, signs up each on Helius, and extracts API keys.

### Buy Gmail accounts only

```bash
python3 helius_key_farmer.py buy --count 10
```

### Sign up Helius for pending accounts

```bash
python3 helius_key_farmer.py signup
```

Processes all accounts in `accounts.json` that haven't been signed up yet.

### Show collected API keys

```bash
python3 helius_key_farmer.py keys
```

### Manually add accounts

```bash
python3 helius_key_farmer.py add
```

Paste accounts in `email|password` format, one per line. Empty line to finish.

## File Structure

```
automated-tools/
  helius_key_farmer.py   # Main automation script
  requirements.txt       # Python dependencies
  .env.example           # Environment template
  .env                   # Your credentials (git-ignored)
  accounts.json          # Bought Gmail accounts (auto-generated, git-ignored)
  helius_keys.json       # Extracted API keys (auto-generated, git-ignored)
```

## How It Works

1. **Anti-detection**: Randomized viewport, spoofed user-agent, human-like typing delays, stealth JS injection
2. **Google OAuth**: Handles email input, password, speedbump/TOS page, and consent screen
3. **Helius onboarding**: Clicks through "Get Started", selects Free plan, creates project
4. **Key extraction**: Intercepts clipboard copy, falls back to DOM parsing and regex matching
5. **Auto .env update**: Appends all keys to `HELIUS_API_KEYS=key1,key2,key3,...`

## Notes

- Runs in **headed mode** (visible browser) for reliability
- Each account gets its own browser context (isolated cookies/sessions)
- If auto-extraction fails, you can paste the API key manually
- Gmail accounts use Google Workspace for Education domains
- Free Helius tier: 1M credits, 10 req/sec

## Disclaimer

This tool is for educational and personal use. Use responsibly and in accordance with the terms of service of the platforms involved.
