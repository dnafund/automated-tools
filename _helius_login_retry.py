"""
Helius retry via /login for accounts already signed up but key extraction failed.
Runs SEQUENTIAL with proper delays. Parallel-safe with _moralis_login_retry.py.
"""
import asyncio
import json
import random
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright
import helius_key_farmer

TOOLS = Path(__file__).parent
ACCOUNTS_FILE = TOOLS / "accounts.json"
HELIUS_KEYS_FILE = TOOLS / "helius_keys.json"
LOG_FILE = TOOLS / "helius_retry.log"

HELIUS_LOGIN_URL = "https://dashboard.helius.dev/login"
HELIUS_API_KEYS_URL = "https://dashboard.helius.dev/api-keys"


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(p):
    return json.load(open(p, encoding="utf-8")) if p.exists() else []


def save_json(p, d):
    json.dump(d, open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False)


async def login_and_extract(browser, email: str, password: str) -> str:
    ctx = await helius_key_farmer.create_stealth_context(browser)
    page = await ctx.new_page()
    try:
        log("   → /login")
        await page.goto(HELIUS_LOGIN_URL, wait_until="domcontentloaded")
        await asyncio.sleep(8)

        # Wait for Google button — text is just "Google" (not "Continue with Google")
        google_btn = page.get_by_role("button", name="Google", exact=True)
        try:
            await google_btn.wait_for(timeout=20000, state="visible")
            log("   Google button found")
        except Exception:
            # Fallback: generic has-text
            google_btn = page.locator('button:has-text("Google")').first
            try:
                await google_btn.wait_for(timeout=10000, state="visible")
            except Exception:
                log("   ❌ No Google button")
                try:
                    await page.screenshot(path=str(TOOLS / "_helius_noGbtn.png"))
                except Exception:
                    pass
                return ""

        # Try popup first, fall back to redirect. Use JS click to bypass interaction checks.
        js_click = """() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const btn = btns.find(b => b.textContent.trim() === 'Google');
            if (btn) { btn.click(); return true; }
            return false;
        }"""
        try:
            async with page.expect_popup(timeout=15000) as popup_info:
                clicked = await page.evaluate(js_click)
                if not clicked:
                    raise Exception("JS click: Google button not found")
            oauth_page = await popup_info.value
            log("   Popup opened via JS click")
        except Exception as e:
            log(f"   Popup attempt failed: {e}")
            try:
                clicked = await page.evaluate(js_click)
                await asyncio.sleep(3)
                oauth_page = page
                log(f"   JS click main page (clicked={clicked})")
            except Exception as e2:
                log(f"   JS click failed: {e2}")
                return ""

        await asyncio.sleep(2)

        # Email step
        try:
            await oauth_page.locator(
                'input#identifierId, input[type="email"]'
            ).first.wait_for(timeout=10000)
            await oauth_page.locator(
                'input#identifierId, input[type="email"]'
            ).first.fill(email)
            await asyncio.sleep(1)
            await oauth_page.locator(
                '#identifierNext, button:has-text("Next")'
            ).first.click()
            log("   Email → Next")
            await asyncio.sleep(4)
        except Exception as e:
            log(f"   Email ERR: {e}")

        # Password step
        try:
            await oauth_page.locator(
                'input[type="password"]:visible'
            ).first.wait_for(timeout=10000)
            await oauth_page.locator(
                'input[type="password"]:visible'
            ).first.fill(password)
            await asyncio.sleep(1)
            await oauth_page.locator(
                '#passwordNext, button:has-text("Next")'
            ).first.click()
            log("   Password → Next")
            await asyncio.sleep(5)
        except Exception as e:
            log(f"   Password ERR: {e}")

        # Wait for popup close / OAuth complete
        for _ in range(8):
            if oauth_page != page and oauth_page.is_closed():
                break
            if oauth_page == page and "dashboard.helius" in page.url:
                break
            for txt in ("Continue", "Allow", "I understood"):
                try:
                    btn = oauth_page.locator(f'button:has-text("{txt}"):visible')
                    if await btn.count() > 0:
                        await btn.first.click()
                        log(f"   Clicked {txt}")
                        await asyncio.sleep(3)
                        break
                except Exception:
                    pass
            await asyncio.sleep(3)

        if oauth_page != page and not oauth_page.is_closed():
            try:
                await oauth_page.close()
            except Exception:
                pass
        log("   Popup closed / OAuth complete")
        await asyncio.sleep(5)

        log(f"   Main URL: {page.url}")
        if "login" in page.url.lower() or "signup" in page.url.lower():
            log("   ❌ Still on login/signup after OAuth")
            return ""

        # Nav to dashboard if not there
        if "onboarding" in page.url or page.url.rstrip('/').endswith("helius.dev"):
            try:
                await page.goto("https://dashboard.helius.dev/dashboard", wait_until="domcontentloaded")
                await asyncio.sleep(5)
                log(f"   Nav to /dashboard: {page.url}")
            except Exception:
                pass

        # ─── Step A: Click "Get Started" on empty dashboard ──────────
        try:
            get_started = page.locator('button:has-text("Get Started")').first
            if await get_started.count() > 0:
                log(f"   Click 'Get Started'")
                await get_started.click(force=True)
                await asyncio.sleep(6)
                log(f"   After Get Started: {page.url}")
        except Exception as e:
            log(f"   Get Started err: {e}")

        # ─── Step B: Free plan button — 2 steps:
        # 1) Click "Sign in with Google or Github" → button morphs
        # 2) Click "Start building" → project created
        for step_label, selector in [
            ("Sign in with Google or Github", 'button:has-text("Sign in with Google or Github"), button:has-text("Sign in with Google")'),
            ("Start building", 'button:has-text("Start building")'),
        ]:
            try:
                btn = page.locator(selector).first
                await btn.wait_for(timeout=6000, state="visible")
                log(f"   Click Free: '{step_label}'")
                await btn.scroll_into_view_if_needed()
                await btn.click(force=True)
                await asyncio.sleep(6)
                log(f"   URL: {page.url}")
            except Exception as e:
                log(f"   '{step_label}' skip: {type(e).__name__}")

        # Screenshot after project created
        try:
            import time as _t
            await page.screenshot(
                path=str(TOOLS / f"_helius_post_project_{int(_t.time())}.png"),
                full_page=True,
            )
        except Exception:
            pass

        # ─── Step C: Click "API Keys" sidebar ──────────────────────────
        try:
            api_keys_link = page.locator('a:has-text("API Keys")').first
            await api_keys_link.wait_for(timeout=10000, state="visible")
            await api_keys_link.click(force=True)
            log(f"   Clicked API Keys sidebar")
            await asyncio.sleep(5)
            log(f"   URL: {page.url}")
        except Exception as e:
            log(f"   Sidebar click err: {e}")
            try:
                await page.goto(HELIUS_API_KEYS_URL, wait_until="domcontentloaded")
                await asyncio.sleep(5)
            except Exception:
                pass

        log(f"   → Extract page: {page.url}")
        key = await helius_key_farmer._extract_api_key(page)
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
    h_keys = load_json(HELIUS_KEYS_FILE)

    pending = [
        a for a in accounts
        if not a.get("helius_signed_up")
    ]

    CONCURRENCY = 5
    log(f"🚀 Helius retry via /login: {len(pending)} accounts, CONCURRENCY={CONCURRENCY}")

    sem = asyncio.Semaphore(CONCURRENCY)
    save_lock = asyncio.Lock()
    stats = {"ok": 0, "fail": 0, "done": 0}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False, slow_mo=80,
            args=["--disable-blink-features=AutomationControlled", "--window-size=1280,800"],
        )

        async def worker(i, acc):
            async with sem:
                email = acc["email"]
                pwd = acc["password"]
                log(f"\n[{i+1}/{len(pending)}] {email}")
                key = await login_and_extract(browser, email, pwd)
                async with save_lock:
                    # Reload to avoid overwriting concurrent writes
                    _h_keys = load_json(HELIUS_KEYS_FILE)
                    _accs = load_json(ACCOUNTS_FILE)
                    if key and len(key) > 15:
                        _h_keys.append({
                            "email": email,
                            "api_key": key,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        })
                        save_json(HELIUS_KEYS_FILE, _h_keys)
                        for a2 in _accs:
                            if a2["email"] == email:
                                a2["helius_signed_up"] = True
                                break
                        save_json(ACCOUNTS_FILE, _accs)
                        stats["ok"] += 1
                        log(f"   ✅ {key[:24]}... | OK total: {stats['ok']}/{stats['done']+1}")
                    else:
                        stats["fail"] += 1
                        log(f"   ❌ Failed | ok={stats['ok']} fail={stats['fail']}")
                    stats["done"] += 1
                await asyncio.sleep(random.uniform(3, 7))

        await asyncio.gather(
            *[worker(i, a) for i, a in enumerate(pending)],
            return_exceptions=True,
        )
        await browser.close()

    log(f"\n🏁 DONE. OK: {stats['ok']} | FAIL: {stats['fail']}")


if __name__ == "__main__":
    asyncio.run(main())
