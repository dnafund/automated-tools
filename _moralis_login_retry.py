"""
Moralis retry via /login — works for accounts already created but key extraction
failed in round 1. Runs SEQUENTIAL (concurrency=1) with proper delays.
"""
import asyncio
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright
import moralis_key_farmer

TOOLS = Path(__file__).parent
ACCOUNTS_FILE = TOOLS / "accounts.json"
MORALIS_KEYS_FILE = TOOLS / "moralis_keys.json"
LOG_FILE = TOOLS / "moralis_retry.log"


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path):
    if path.exists():
        return json.load(open(path, encoding="utf-8"))
    return []


def save_json(path, data):
    json.dump(data, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)


async def login_and_extract(browser, email: str, password: str) -> str:
    """Login Moralis via /login URL + Google OAuth, extract API key."""
    ctx = await moralis_key_farmer.create_stealth_context(browser)
    page = await ctx.new_page()
    try:
        log(f"   → /login")
        await page.goto("https://admin.moralis.com/login", wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # Click Login with Google
        async with page.expect_popup(timeout=10000) as popup_info:
            await page.locator('button:has-text("Login with Google")').first.click()
        popup = await popup_info.value
        log(f"   Popup opened")
        await asyncio.sleep(2)

        # Email step
        try:
            await popup.locator('input#identifierId, input[type="email"]').first.wait_for(timeout=10000)
            await popup.locator('input#identifierId, input[type="email"]').first.fill(email)
            await asyncio.sleep(1)
            await popup.locator('#identifierNext, button:has-text("Next")').first.click()
            log(f"   Email → Next")
            await asyncio.sleep(4)
        except Exception as e:
            log(f"   Email step ERR: {e}")

        # Password step
        try:
            await popup.locator('input[type="password"]:visible').first.wait_for(timeout=10000)
            await popup.locator('input[type="password"]:visible').first.fill(password)
            await asyncio.sleep(1)
            await popup.locator('#passwordNext, button:has-text("Next")').first.click()
            log(f"   Password → Next")
            await asyncio.sleep(5)
        except Exception as e:
            log(f"   Password step ERR: {e}")

        # Wait for popup close (up to 30s)
        for _ in range(10):
            if popup.is_closed():
                break
            # Click Continue/Allow if consent page
            for txt in ("Continue", "Allow", "I understood"):
                try:
                    btn = popup.locator(f'button:has-text("{txt}"):visible')
                    if await btn.count() > 0:
                        await btn.first.click()
                        log(f"   Clicked {txt}")
                        await asyncio.sleep(3)
                        break
                except Exception:
                    pass
            await asyncio.sleep(3)

        if not popup.is_closed():
            try:
                await popup.close()
            except Exception:
                pass
        log(f"   Popup closed")

        # Wait for main page to settle
        await asyncio.sleep(5)
        log(f"   Main URL: {page.url}")

        if "login" in page.url.lower():
            log(f"   ❌ Still on login after OAuth")
            return ""

        # Go to API Keys page
        await page.goto("https://admin.moralis.com/web3-apis", wait_until="domcontentloaded")
        await asyncio.sleep(4)
        log(f"   → /web3-apis: {page.url}")

        # Extract key — reuse existing moralis extraction
        key = await moralis_key_farmer._extract_moralis_key(page)
        return key
    except Exception as e:
        log(f"   CRASH: {e}")
        return ""
    finally:
        try:
            await ctx.close()
        except Exception:
            pass


async def main():
    accounts = load_json(ACCOUNTS_FILE)
    m_keys = load_json(MORALIS_KEYS_FILE)

    pending = [
        a for a in accounts
        if not a.get("moralis_signed_up")
    ]

    log(f"🚀 Moralis retry via /login: {len(pending)} accounts, SEQUENTIAL")

    ok, fail = 0, 0
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False, slow_mo=80,
            args=["--disable-blink-features=AutomationControlled", "--window-size=1400,900"],
        )

        for i, acc in enumerate(pending):
            email = acc["email"]
            pwd = acc["password"]
            log(f"\n[{i+1}/{len(pending)}] {email}")
            key = await login_and_extract(browser, email, pwd)
            if key and len(key) > 15:
                m_keys.append({
                    "email": email,
                    "api_key": key,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                save_json(MORALIS_KEYS_FILE, m_keys)
                acc["moralis_signed_up"] = True
                save_json(ACCOUNTS_FILE, accounts)
                ok += 1
                log(f"   ✅ {key[:24]}... | Total: {ok}")
            else:
                fail += 1
                log(f"   ❌ Failed | ok={ok} fail={fail}")

            # Cooldown
            await asyncio.sleep(random.uniform(5, 10))

        await browser.close()

    log(f"\n🏁 DONE. OK: {ok} | FAIL: {fail}")


if __name__ == "__main__":
    asyncio.run(main())
