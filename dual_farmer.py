"""
Dual API Key Farmer — Helius + Moralis simultaneous signup

Speed: 50% faster delays | Health check: every 10 min | Auto-recovery

For each account:
  - Helius signup (context A) + Moralis signup (context B) run CONCURRENTLY
  - Both use separate browser contexts in the same browser
  - Progress saved after every account

Usage:
    python3 dual_farmer.py add              # Bulk add accounts (email|password)
    python3 dual_farmer.py signup            # Run dual signup for all pending
    python3 dual_farmer.py signup --seq      # Sequential mode (safer for Google)
    python3 dual_farmer.py status            # Show progress
    python3 dual_farmer.py retry             # Retry failed accounts
"""

import asyncio
import json
import argparse
import random
import sys
import time
import re
from datetime import datetime, timezone
from pathlib import Path


# ── Paths ──────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
HELIUS_KEYS_FILE = DATA_DIR / "helius_keys.json"
MORALIS_KEYS_FILE = DATA_DIR / "moralis_keys.json"
PROGRESS_FILE = DATA_DIR / "dual_progress.json"
LOG_FILE = DATA_DIR / "dual_farmer.log"

# Speed factor: 0.5 = all delays halved (0.3 triggers CAPTCHA)
SPEED = 0.5
HEALTH_INTERVAL = 600  # 10 minutes


# ── JSON helpers ───────────────────────────────────────────────────
def load_json(path: Path) -> list:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


def save_json(path: Path, data) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Logging ────────────────────────────────────────────────────────
def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── Speed-optimized delay helpers ──────────────────────────────────
async def fast_delay(min_s: float = 0.5, max_s: float = 2.0):
    """Human delay reduced by SPEED factor."""
    await asyncio.sleep(random.uniform(min_s * SPEED, max_s * SPEED))


async def fast_type(page_or_locator, text: str, min_delay=15, max_delay=45):
    """Fast human typing: 15-45ms per char (was 50-150ms)."""
    for char in text:
        await page_or_locator.press(char)
        await asyncio.sleep(random.uniform(min_delay, max_delay) / 1000)


# ── Import & monkey-patch existing farmers for speed ───────────────
sys.path.insert(0, str(DATA_DIR))
import helius_key_farmer
import moralis_key_farmer

helius_key_farmer.human_delay = fast_delay
helius_key_farmer.human_type = fast_type
moralis_key_farmer.human_delay = fast_delay
moralis_key_farmer.human_type = fast_type


# ── Enhanced Moralis signup with popup handling ────────────────────
async def _moralis_signup_with_popup(browser, email: str, password: str, is_retry: bool = False) -> str:
    """
    Moralis signup with robust Google OAuth popup handling.
    Moralis opens Google as a popup window (not redirect).
    We intercept the popup and do OAuth there.

    is_retry: if True, uses /login URL and re-auth flow for accounts that
              already exist on Moralis but key extraction failed previously.
    """
    ctx = await moralis_key_farmer.create_stealth_context(browser)
    page = await ctx.new_page()

    try:
        # Step 1: Open login or register page
        if is_retry:
            log(f"   [M] Opening login (retry)...")
            await page.goto(moralis_key_farmer.MORALIS_LOGIN_URL, wait_until="domcontentloaded")
        else:
            log(f"   [M] Opening register...")
            await page.goto(moralis_key_farmer.MORALIS_REGISTER_URL, wait_until="domcontentloaded")
        await fast_delay(2, 3)

        # Step 2: Click "Login with Google"
        google_btn = page.locator(
            'button:has-text("Login with Google"), '
            'button:has-text("Google"), '
            'a:has-text("Login with Google"), '
            'a:has-text("Google"), '
            '[data-testid*="google" i]'
        )
        if await google_btn.count() == 0:
            log("   [M] Google button not found")
            return ""

        await fast_delay(0.3, 0.8)

        # Try popup-based OAuth first, fall back to redirect
        oauth_page = page
        try:
            async with page.expect_popup(timeout=10000) as popup_info:
                await google_btn.first.click()
            popup = await popup_info.value
            oauth_page = popup
            log("   [M] Google opened as popup")
        except Exception:
            # No popup — Google opened as redirect on same page
            await google_btn.first.click()
            await fast_delay(2, 3)
            log("   [M] Google opened as redirect")

        # Step 3: Google OAuth on whichever page has it
        oauth_ok = await moralis_key_farmer._google_oauth(oauth_page, email, password)

        # If popup was used, close it and check main page
        if oauth_page != page:
            try:
                await oauth_page.wait_for_event("close", timeout=15000)
            except Exception:
                pass
            await fast_delay(2, 3)

        # Wait for redirect back to Moralis
        await fast_delay(2, 4)
        if "moralis.com" not in page.url:
            await fast_delay(3, 5)
            if "moralis.com" not in page.url:
                log(f"   [M] OAuth failed — URL: {page.url[:60]}")
                return ""

        log("   [M] Google OAuth complete")
        await fast_delay(1, 2)

        # Retry login if still on login/register page (account already exists)
        # This happens for accounts that completed signup but extraction failed previously.
        for _retry in range(2):
            if any(x in page.url for x in ("/login", "/register", "/auth")):
                log(f"   [M] Still on login page, forcing nav to dashboard")
                try:
                    await page.goto(moralis_key_farmer.MORALIS_DASHBOARD_URL, wait_until="domcontentloaded")
                    await fast_delay(2, 4)
                except Exception:
                    pass
                # If still login after goto, try Google button again
                if any(x in page.url for x in ("/login", "/register", "/auth")):
                    try:
                        gbtn = page.locator(
                            'button:has-text("Login with Google"), '
                            'button:has-text("Google"), '
                            'a:has-text("Login with Google"), '
                            'a:has-text("Google")'
                        )
                        if await gbtn.count() > 0:
                            try:
                                async with page.expect_popup(timeout=8000) as popup_info:
                                    await gbtn.first.click()
                                popup2 = await popup_info.value
                                await moralis_key_farmer._google_oauth(popup2, email, password)
                                try:
                                    await popup2.wait_for_event("close", timeout=15000)
                                except Exception:
                                    pass
                            except Exception:
                                await gbtn.first.click()
                                await fast_delay(4, 6)
                    except Exception:
                        pass
            else:
                break

        # Step 4: Onboarding
        current_url = page.url
        if "onboarding" in current_url:
            await _fast_moralis_onboarding(page)
        elif "register" in current_url:
            await fast_delay(3, 5)
            current_url = page.url
            if "onboarding" in current_url:
                await _fast_moralis_onboarding(page)
            else:
                await page.goto(moralis_key_farmer.MORALIS_DASHBOARD_URL, wait_until="domcontentloaded")
                await fast_delay(2, 3)
                if "onboarding" in page.url:
                    await _fast_moralis_onboarding(page)
        else:
            body = await page.inner_text("body")
            if "Hello there" in body or "onboarding" in page.url:
                await _fast_moralis_onboarding(page)

        # Step 5: Extract API key
        api_key = await moralis_key_farmer._extract_moralis_key(page)
        return api_key

    except Exception as e:
        log(f"   [M] Error: {e}")
        try:
            await page.screenshot(path=str(DATA_DIR / f"error_m_{email.split('@')[0]}.png"))
        except Exception:
            pass
        return ""
    finally:
        await ctx.close()


async def _fast_moralis_onboarding(page):
    """Streamlined Moralis onboarding — pick random options fast."""
    log("   [M] Onboarding...")
    await fast_delay(0.5, 1)

    # Fill dropdowns by clicking "Select an option" placeholders
    for dropdown_idx in range(4):
        try:
            placeholder = page.locator('text="Select an option"').nth(0)
            if await placeholder.count() == 0:
                break
            await placeholder.click(force=True, timeout=3000)
            await fast_delay(0.3, 0.8)

            options = page.locator(
                '[class*="option"]:visible, '
                'li[role="option"]:visible, '
                'div[role="option"]:visible'
            )
            opt_count = await options.count()
            if opt_count > 0:
                pick = random.randint(0, min(opt_count - 1, 3))
                await options.nth(pick).click()
                await fast_delay(0.2, 0.5)
        except Exception:
            break

    # Telegram input
    try:
        tg = page.locator(
            'input[placeholder*="Type here"], '
            'input[placeholder*="telegram" i], '
            'input:near(:text("telegram"))'
        ).first
        if await tg.count() > 0:
            await tg.click()
            await fast_type(tg, "@1")
    except Exception:
        pass

    await fast_delay(0.3, 0.5)

    # Force-enable and click Next
    try:
        await page.evaluate("""
            document.querySelectorAll('button').forEach(btn => {
                if (btn.textContent.trim().toLowerCase().includes('next')) {
                    btn.disabled = false;
                    btn.removeAttribute('disabled');
                }
            });
        """)
        next_btn = page.locator('button:has-text("Next")')
        await next_btn.first.click(force=True)
        log("   [M] Clicked Next")
        await fast_delay(2, 3)
    except Exception as e:
        log(f"   [M] Next button issue: {e}")

    # Select Free Plan
    await fast_delay(1, 2)
    try:
        free_btn = page.locator(
            'div:has(> :text("Free")) button:has-text("Get Started"), '
            'button:has-text("Get Started")'
        )
        if await free_btn.count() > 0:
            await free_btn.first.click()
            log("   [M] Selected Free Plan")
            await fast_delay(2, 3)
    except Exception:
        pass

    # Add Card Later
    try:
        skip = page.locator(
            'button:has-text("Add Card Later"), '
            'a:has-text("Add Card Later"), '
            'button:has-text("Skip")'
        )
        await skip.first.wait_for(timeout=8000)
        await fast_delay(0.3, 0.8)
        await skip.first.click()
        log("   [M] Skipped payment")
        await fast_delay(2, 3)
    except Exception:
        pass


# ── Process one account ───────────────────────────────────────────
async def _process_account(browser, account, sequential=False):
    """Signup for both services. Returns dict with keys."""
    email = account["email"]
    password = account["password"]
    skip_h = account.get("helius_signed_up", False)
    skip_m = account.get("moralis_signed_up", False)

    results = {"helius": "", "moralis": ""}

    async def do_helius():
        if skip_h:
            return
        try:
            key = await helius_key_farmer._signup_helius_for_account(browser, email, password)
            results["helius"] = key or ""
        except Exception as e:
            log(f"   [H] Error: {e}")

    async def do_moralis():
        if skip_m:
            return
        try:
            key = await _moralis_signup_with_popup(browser, email, password)
            results["moralis"] = key or ""
        except Exception as e:
            log(f"   [M] Error: {e}")

    if sequential:
        await do_helius()
        await fast_delay(1, 2)
        await do_moralis()
    else:
        await asyncio.gather(do_helius(), do_moralis())

    return results


# ── Health check ──────────────────────────────────────────────────
def _health_check(stats):
    elapsed = time.time() - stats["started"]
    mins = elapsed / 60
    rate = stats["done"] / mins if mins > 1 else 0
    remaining = stats["total"] - stats["done"]
    eta = remaining / rate if rate > 0 else 0

    log(f"\n{'='*55}")
    log(f"  HEALTH CHECK — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"  Progress: {stats['done']}/{stats['total']} ({stats['done']*100//max(stats['total'],1)}%)")
    log(f"  Helius:  OK {stats['helius_ok']}  |  FAIL {len(stats['helius_fail'])}")
    log(f"  Moralis: OK {stats['moralis_ok']}  |  FAIL {len(stats['moralis_fail'])}")
    log(f"  Speed: {rate:.1f} accounts/min  |  ETA: {eta:.0f} min ({eta/60:.1f}h)")
    log(f"  Elapsed: {mins:.0f} min ({mins/60:.1f}h)")
    log(f"{'='*55}\n")


# ── Main signup loop ─────────────────────────────────────────────
async def signup_all(sequential=False):
    from playwright.async_api import async_playwright

    # Force non-interactive (skip manual input prompts in imported scripts)
    import os as _os
    sys.stdin = open(_os.devnull, "r")

    accounts = load_json(ACCOUNTS_FILE)
    h_keys = load_json(HELIUS_KEYS_FILE)
    m_keys = load_json(MORALIS_KEYS_FILE)

    pending = [
        a for a in accounts
        if not a.get("helius_signed_up") or not a.get("moralis_signed_up")
    ]

    if not pending:
        log("No pending accounts. Use 'add' to add accounts first.")
        return

    mode = "sequential" if sequential else "parallel"
    log(f"🚀 Dual signup: {len(pending)} accounts | Mode: {mode} | Speed: +50%")
    log(f"   Helius keys: {len(h_keys)} | Moralis keys: {len(m_keys)}")

    stats = {
        "started": time.time(),
        "total": len(pending),
        "done": 0,
        "helius_ok": 0,
        "moralis_ok": 0,
        "helius_fail": [],
        "moralis_fail": [],
        "last_health": time.time(),
    }
    save_json(PROGRESS_FILE, stats)

    # Concurrency: 2 accounts in parallel (5 triggers Google/Helius bot detection)
    CONCURRENCY = 2
    sem = asyncio.Semaphore(CONCURRENCY)
    save_lock = asyncio.Lock()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            slow_mo=random.randint(8, 20),
            args=["--disable-blink-features=AutomationControlled", "--window-size=1280,800"],
        )

        async def _one_account(i, account):
            async with sem:
                email = account["email"]
                skip_h = account.get("helius_signed_up", False)
                skip_m = account.get("moralis_signed_up", False)
                services = []
                if not skip_h:
                    services.append("H")
                if not skip_m:
                    services.append("M")

                log(f"\n[{i+1}/{len(pending)}] {email} [{'+'.join(services)}]")

                try:
                    results = await _process_account(browser, account, sequential)
                except Exception as e:
                    log(f"   CRASH {email}: {e}")
                    results = {"helius": "", "moralis": ""}

                async with save_lock:
                    # Save Helius key
                    if results["helius"]:
                        h_keys.append({
                            "email": email,
                            "api_key": results["helius"],
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        })
                        save_json(HELIUS_KEYS_FILE, h_keys)
                        account["helius_signed_up"] = True
                        stats["helius_ok"] += 1
                        log(f"   ✅ [H] {email} {results['helius'][:20]}...")
                    elif not skip_h:
                        stats["helius_fail"].append(email)
                        log(f"   ❌ [H] {email} Failed")

                    # Save Moralis key
                    if results["moralis"]:
                        m_keys.append({
                            "email": email,
                            "api_key": results["moralis"],
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        })
                        save_json(MORALIS_KEYS_FILE, m_keys)
                        account["moralis_signed_up"] = True
                        stats["moralis_ok"] += 1
                        log(f"   ✅ [M] {email} {results['moralis'][:20]}...")
                    elif not skip_m:
                        stats["moralis_fail"].append(email)
                        log(f"   ❌ [M] {email} Failed")

                    save_json(ACCOUNTS_FILE, accounts)
                    stats["done"] += 1
                    save_json(PROGRESS_FILE, stats)

                    now = time.time()
                    if now - stats["last_health"] >= HEALTH_INTERVAL:
                        _health_check(stats)
                        stats["last_health"] = now

                # Small jitter to desync concurrent workers
                await asyncio.sleep(random.uniform(2, 5))

        log(f"🚀 Concurrency: {CONCURRENCY} accounts in parallel")
        await asyncio.gather(
            *[_one_account(i, a) for i, a in enumerate(pending)],
            return_exceptions=True,
        )

        await browser.close()

    # Final report
    _health_check(stats)
    log(f"\n🏁 DONE — Helius: {stats['helius_ok']} | Moralis: {stats['moralis_ok']}")

    # Update .env
    try:
        helius_key_farmer._update_env_file(h_keys)
        moralis_key_farmer._update_env_file(m_keys)
    except Exception:
        pass


# ── Signup with auto-recovery ─────────────────────────────────────
async def signup_with_recovery(sequential=False):
    """Auto-restart on crash."""
    max_retries = 5
    for attempt in range(max_retries):
        try:
            await signup_all(sequential)
            break
        except Exception as e:
            log(f"\n💥 CRASH (attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                log("♻️  Restarting in 30s...")
                await asyncio.sleep(30)
            else:
                log("❌ Max retries reached. Check errors and run again.")


# ── Retry failed ─────────────────────────────────────────────────
async def retry_failed():
    from playwright.async_api import async_playwright

    import os as _os
    sys.stdin = open(_os.devnull, "r")

    progress = load_json(PROGRESS_FILE) if PROGRESS_FILE.exists() else {}
    if not progress:
        log("No progress file. Run 'signup' first.")
        return

    accounts = load_json(ACCOUNTS_FILE)
    h_keys = load_json(HELIUS_KEYS_FILE)
    m_keys = load_json(MORALIS_KEYS_FILE)

    failed_emails = set(progress.get("helius_fail", [])) | set(progress.get("moralis_fail", []))
    retry_list = [a for a in accounts if a["email"] in failed_emails]

    if not retry_list:
        log("No failed accounts to retry.")
        return

    log(f"🔄 Retrying {len(retry_list)} failed accounts...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=random.randint(8, 20), args=["--disable-blink-features=AutomationControlled", "--window-size=1280,800"])

        for i, account in enumerate(retry_list):
            email = account["email"]
            log(f"\n[RETRY {i+1}/{len(retry_list)}] {email}")

            results = await _process_account(browser, account, sequential=True)

            if results["helius"]:
                h_keys.append({
                    "email": email,
                    "api_key": results["helius"],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                save_json(HELIUS_KEYS_FILE, h_keys)
                account["helius_signed_up"] = True
                log(f"   ✅ [H] {results['helius'][:20]}...")

            if results["moralis"]:
                m_keys.append({
                    "email": email,
                    "api_key": results["moralis"],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                save_json(MORALIS_KEYS_FILE, m_keys)
                account["moralis_signed_up"] = True
                log(f"   ✅ [M] {results['moralis'][:20]}...")

            save_json(ACCOUNTS_FILE, accounts)
            cooldown = random.uniform(8, 15)
            log(f"   ⏳ Cooldown {cooldown:.0f}s...")
            await asyncio.sleep(cooldown)

        await browser.close()

    log("🏁 Retry complete.")


# ── Bulk add accounts ────────────────────────────────────────────
def add_accounts():
    """Add accounts from stdin. Format: email|password per line."""
    accounts = load_json(ACCOUNTS_FILE)
    existing_emails = {a["email"] for a in accounts}

    print("📝 Paste accounts (email|password), empty line or Ctrl+D to finish:")
    added = 0
    dupes = 0
    while True:
        try:
            line = input().strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break

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
            if added % 50 == 0:
                print(f"   ... {added} added")

    save_json(ACCOUNTS_FILE, accounts)
    print(f"\n📊 Added: {added} | Duplicates skipped: {dupes} | Total: {len(accounts)}")


# ── Status ───────────────────────────────────────────────────────
def show_status():
    accounts = load_json(ACCOUNTS_FILE)
    h_keys = load_json(HELIUS_KEYS_FILE)
    m_keys = load_json(MORALIS_KEYS_FILE)

    h_pending = sum(1 for a in accounts if not a.get("helius_signed_up"))
    m_pending = sum(1 for a in accounts if not a.get("moralis_signed_up"))

    print(f"📊 Status:")
    print(f"   Total accounts: {len(accounts)}")
    print(f"   Helius:  {len(h_keys)} keys  |  {h_pending} pending")
    print(f"   Moralis: {len(m_keys)} keys  |  {m_pending} pending")

    if PROGRESS_FILE.exists():
        p = load_json(PROGRESS_FILE)
        if p:
            print(f"\n   Last run: {p.get('done', 0)}/{p.get('total', 0)}")
            print(f"   H ok: {p.get('helius_ok', 0)}  fail: {len(p.get('helius_fail', []))}")
            print(f"   M ok: {p.get('moralis_ok', 0)}  fail: {len(p.get('moralis_fail', []))}")

    # Show recent log
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text().strip().split("\n")
        if lines:
            print(f"\n   Last log entry: {lines[-1]}")


# ── CLI ──────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Dual API Key Farmer (Helius + Moralis)")
    sub = parser.add_subparsers(dest="command")

    signup_p = sub.add_parser("signup", help="Run dual signup for pending accounts")
    signup_p.add_argument("--seq", action="store_true", help="Sequential mode (safer)")

    sub.add_parser("add", help="Bulk add accounts (email|password)")
    sub.add_parser("status", help="Show progress")
    sub.add_parser("retry", help="Retry failed accounts")

    args = parser.parse_args()

    if args.command == "signup":
        asyncio.run(signup_with_recovery(sequential=args.seq))
    elif args.command == "retry":
        asyncio.run(retry_failed())
    elif args.command == "add":
        add_accounts()
    elif args.command == "status":
        show_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
