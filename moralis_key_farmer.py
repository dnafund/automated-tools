"""
Moralis API Key Farmer
Automates: signup Moralis via Google OAuth → complete onboarding → select Free plan → extract API key.

Flow (verified from video):
  1. admin.moralis.com/register → click "Login with Google"
  2. Google Sign in: email → password → "I understood" speedbump → Welcome page
  3. Redirect to Moralis → onboarding form:
     - Role: "Developer"
     - Company: "I'm an individual developer-'it's only me'"
     - Where heard: "Twitter"
     - Telegram: "@1"
     → click "Next"
  4. Select Plan → click "Get Started" on Free plan
  5. Add Payment Option → click "Add Card Later"
  6. Dashboard shows API Key (hidden) → click copy/eye icon → extract

Requirements:
    pip install playwright
    playwright install chromium

Usage:
    python3 moralis_key_farmer.py signup
    python3 moralis_key_farmer.py keys
    python3 moralis_key_farmer.py add

Files:
    tools/accounts.json      — Gmail accounts [{email, password, recovery}]
    tools/moralis_keys.json  — Moralis API keys [{email, api_key}]
"""

import asyncio
import json
import argparse
import os
import re
import random
import sys
from datetime import datetime, timezone
from pathlib import Path


DATA_DIR = Path(__file__).parent
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
KEYS_FILE = DATA_DIR / "moralis_keys.json"

MORALIS_REGISTER_URL = "https://admin.moralis.com/register"
MORALIS_LOGIN_URL = "https://admin.moralis.com/login"
MORALIS_DASHBOARD_URL = "https://admin.moralis.com"
MORALIS_ONBOARDING_URL = "https://admin.moralis.com/onboarding"
MORALIS_API_KEYS_URL = "https://admin.moralis.com/api-keys"


# ─── Anti-detect helpers ─────────────────────────────────────────

async def human_delay(min_s: float = 0.5, max_s: float = 2.0):
    """Random delay to mimic human behavior."""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def human_type(page_or_locator, text: str, min_delay=50, max_delay=150):
    """Type text character by character with random delay (ms)."""
    for char in text:
        await page_or_locator.press(char)
        await asyncio.sleep(random.uniform(min_delay, max_delay) / 1000)


async def create_stealth_context(browser):
    """Create a browser context with anti-detect settings."""
    width = random.randint(1280, 1440)
    height = random.randint(800, 900)

    context = await browser.new_context(
        viewport={"width": width, "height": height},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{random.randint(120, 130)}.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        timezone_id="America/New_York",
        color_scheme="dark",
        permissions=["clipboard-read", "clipboard-write"],
    )

    # Stealth JS — hide webdriver flags
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
        window.chrome = { runtime: {} };
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) =>
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters);
    """)

    return context


def load_json(path: Path) -> list:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


def save_json(path: Path, data: list) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ─── Google OAuth flow ───────────────────────────────────────────

async def _google_oauth(page, email: str, password: str) -> bool:
    """Handle Google OAuth: account chooser → email → password → speedbump → consent.
    Returns True if we got past Google and redirected back to Moralis.
    """

    # Step 0: Account Chooser — if Google shows list of existing accounts,
    # click the one matching our email (this skips email + password steps).
    try:
        await human_delay(1, 2)
        if "accountchooser" in page.url or "oauth/selectaccount" in page.url:
            print("   👥 Account chooser detected")
            # Each account is a clickable div/li with email text
            candidates = page.locator(
                f'[data-email="{email}" i], '
                f'div[role="link"]:has-text("{email}"), '
                f'li:has-text("{email}"), '
                f'*:text-is("{email}")'
            )
            c_count = await candidates.count()
            if c_count > 0:
                await candidates.first.click()
                print(f"   ✅ Picked account: {email}")
                await human_delay(3, 5)
            else:
                # Fallback: click "Use another account" / "Add account"
                other = page.locator(
                    'div:has-text("Use another account"), '
                    'li:has-text("Use another account"), '
                    'div:has-text("Add account"), '
                    'a:has-text("Use another account")'
                )
                if await other.count() > 0:
                    await other.first.click()
                    print("   ➕ Use another account")
                    await human_delay(2, 4)
    except Exception as e:
        print(f"   ⚠️  Chooser step issue: {e}")

    # Early exit: if chooser already redirected us back to Moralis, done.
    # Check hostname only — popup URL has moralis.com in query string (redirect target)
    await human_delay(0.5, 1)
    try:
        from urllib.parse import urlparse
        host = urlparse(page.url).hostname or ""
    except Exception:
        host = ""
    if host.endswith("moralis.com") or page.is_closed():
        return True

    # Step 1: Google email
    email_input = page.locator(
        'input#identifierId, input[type="email"], input[name="identifier"]'
    ).first
    try:
        await email_input.wait_for(timeout=15000)
        await human_delay(0.5, 1)

        current_val = await email_input.input_value()
        if current_val and "@" in current_val:
            print(f"   📧 Email pre-filled: {current_val}")
        else:
            await email_input.click()
            await human_delay(0.3, 0.6)
            await human_type(email_input, email)
            print(f"   📧 Email: {email}")

        await human_delay(0.5, 1)
        next_btn = page.locator(
            '#identifierNext, button:has-text("Next"), button:has-text("Tiếp theo")'
        )
        await next_btn.first.click()
        await human_delay(2, 4)
    except Exception as e:
        print(f"   ⚠️  Email step issue: {e}")
        try:
            next_btn = page.locator(
                '#identifierNext, button:has-text("Next"), button:has-text("Tiếp theo")'
            )
            if await next_btn.count() > 0:
                await next_btn.first.click()
                await human_delay(2, 4)
        except Exception:
            pass

    # Step 2: Google password
    pw_input = page.locator(
        'input[type="password"]:visible, input[name="Passwd"]:visible'
    ).first
    try:
        await pw_input.wait_for(timeout=15000)
        await human_delay(0.5, 1)
        await pw_input.click()
        await human_delay(0.3, 0.6)
        await human_type(pw_input, password)
        await human_delay(0.5, 1)
        next_btn = page.locator(
            '#passwordNext, button:has-text("Next"), button:has-text("Tiếp theo")'
        )
        await next_btn.first.click()
        print("   🔑 Password entered")
        await human_delay(3, 6)
    except Exception as e:
        print(f"   ⚠️  Password step issue: {e}")

    # Step 3: Speedbump "I understood" — bypass scroll with JS
    await human_delay(2, 3)
    current_url = page.url
    if "speedbump" in current_url or "gaplustos" in current_url:
        print("   📜 Speedbump → force click via JS...")
        try:
            await page.evaluate("""
                const btns = [...document.querySelectorAll(
                    'button, input[type="button"], input[type="submit"]'
                )];
                const btn = btns.find(b => {
                    const t = (b.textContent || b.value || '').toLowerCase();
                    return t.includes('hiểu') || t.includes('understand')
                        || t.includes('i understood');
                });
                if (btn) {
                    btn.disabled = false;
                    btn.removeAttribute('disabled');
                    btn.click();
                }
            """)
            print("   ✅ Clicked 'I understood'")
            await human_delay(3, 5)
        except Exception:
            pass

    # Step 4: "Welcome to your new account" page — just wait & continue
    await human_delay(2, 3)
    current_url = page.url
    if "welcome" in current_url.lower() or "accounts.google.com" in current_url:
        # Try clicking "Continue" or any button to proceed
        try:
            continue_btn = page.locator(
                'button:has-text("Continue"), '
                'button:has-text("Tiếp tục"), '
                'a:has-text("Continue"), '
                'input[type="submit"]'
            )
            if await continue_btn.count() > 0:
                await continue_btn.first.click()
                print("   ✅ Clicked Continue on Welcome page")
                await human_delay(3, 5)
        except Exception:
            pass

    # Step 5: OAuth consent — "Continue" / "Allow"
    await human_delay(1, 2)
    try:
        consent = page.locator(
            'button:has-text("Continue"), '
            'button:has-text("Allow"), '
            'button:has-text("Cho phép"), '
            '#submit_approve_access'
        )
        await consent.first.wait_for(timeout=8000)
        await human_delay(1, 2)
        await consent.first.click()
        print("   ✅ OAuth consent")
        await human_delay(3, 6)
    except Exception:
        # No consent page needed — already authorized or auto-redirect
        pass

    # Wait for redirect back to Moralis
    await human_delay(3, 5)
    print(f"   📍 URL: {page.url[:80]}")

    return "moralis.com" in page.url


# ─── Moralis onboarding ──────────────────────────────────────────

async def _complete_onboarding(page) -> bool:
    """Complete Moralis onboarding form if it appears.
    Step 1: Configure Experience (role, company, where heard, telegram)
    Step 2: Select Plan (Free)
    Step 3: Add Card Later
    Returns True if onboarding was completed or skipped.
    """

    await human_delay(2, 3)
    current_url = page.url

    # Check if we're on onboarding or dashboard
    if "onboarding" not in current_url:
        # Maybe already on dashboard — check if onboarding is needed
        try:
            await page.goto(MORALIS_ONBOARDING_URL, wait_until="domcontentloaded")
            await human_delay(2, 3)
        except Exception:
            pass

    current_url = page.url
    if "onboarding" not in current_url:
        print("   ℹ️  No onboarding needed — already on dashboard")
        return True

    print("   📋 Onboarding form...")

    # ── Step 1: Configure Your Experience ──

    # Wait for form to load
    await human_delay(1, 2)

    # Role dropdown — select "Developer"
    try:
        role_select = page.locator(
            'select:near(:text("current role")), '
            '[class*="select"]:near(:text("current role"))'
        )

        # Try clicking the dropdown wrapper first (React/custom selects)
        role_wrapper = page.locator(
            'div:has(> div:text("Select an option")):near(:text("current role"))'
        ).first

        # Strategy: Click the first dropdown area
        dropdowns = page.locator('[class*="select__control"], [class*="Select"], select')
        dropdown_count = await dropdowns.count()

        if dropdown_count >= 3:
            # First dropdown = role
            await dropdowns.nth(0).click()
            await human_delay(0.5, 1)

            # Select "Developer"
            developer_opt = page.locator(
                '[class*="option"]:has-text("Developer"), '
                'option:has-text("Developer"), '
                'li:has-text("Developer"), '
                'div[role="option"]:has-text("Developer")'
            )
            if await developer_opt.count() > 0:
                # Pick "Developer" (not "Senior Developer")
                for i in range(await developer_opt.count()):
                    text = await developer_opt.nth(i).inner_text()
                    if text.strip() == "Developer":
                        await developer_opt.nth(i).click()
                        break
                else:
                    await developer_opt.last.click()
                print("   ✅ Role: Developer")
            else:
                # Try CEO/Executive as fallback
                ceo_opt = page.locator(
                    '[class*="option"]:has-text("CEO"), '
                    'option:has-text("CEO"), '
                    'div[role="option"]:has-text("CEO")'
                )
                if await ceo_opt.count() > 0:
                    await ceo_opt.first.click()
                    print("   ✅ Role: CEO/Executive")

            await human_delay(0.5, 1)

            # Second dropdown = company size
            await dropdowns.nth(1).click()
            await human_delay(0.5, 1)

            individual_opt = page.locator(
                '[class*="option"]:has-text("individual"), '
                'option:has-text("individual"), '
                'li:has-text("individual"), '
                'div[role="option"]:has-text("individual")'
            )
            if await individual_opt.count() > 0:
                await individual_opt.first.click()
                print("   ✅ Company: Individual developer")

            await human_delay(0.5, 1)

            # Third dropdown = where heard
            await dropdowns.nth(2).click()
            await human_delay(0.5, 1)

            twitter_opt = page.locator(
                '[class*="option"]:has-text("Twitter"), '
                'option:has-text("Twitter"), '
                'li:has-text("Twitter"), '
                'div[role="option"]:has-text("Twitter")'
            )
            if await twitter_opt.count() > 0:
                await twitter_opt.first.click()
                print("   ✅ Where heard: Twitter")

            await human_delay(0.5, 1)
        else:
            # Fallback: try native <select> elements
            selects = page.locator("select")
            sel_count = await selects.count()
            if sel_count >= 3:
                await selects.nth(0).select_option(label="Developer")
                print("   ✅ Role: Developer")
                await human_delay(0.3, 0.6)

                # Company size
                await selects.nth(1).select_option(index=1)
                print("   ✅ Company: selected")
                await human_delay(0.3, 0.6)

                # Where heard
                await selects.nth(2).select_option(label="Twitter")
                print("   ✅ Where heard: Twitter")
                await human_delay(0.3, 0.6)

    except Exception as e:
        print(f"   ⚠️  Dropdown fill issue: {e}")

    # Telegram username
    try:
        telegram_input = page.locator(
            'input[placeholder*="Type here"], '
            'input[placeholder*="telegram" i], '
            'input:near(:text("telegram"))'
        ).first
        await telegram_input.click()
        await human_delay(0.3, 0.6)
        await human_type(telegram_input, "@1")
        print("   ✅ Telegram: @1")
    except Exception as e:
        print(f"   ⚠️  Telegram input issue: {e}")

    await human_delay(0.5, 1)

    # Click "Next" button
    try:
        next_btn = page.locator(
            'button:has-text("Next"), '
            'button:has-text("next"), '
            'a:has-text("Next")'
        )
        await next_btn.first.click()
        print("   ✅ Clicked Next → Select Plan")
        await human_delay(3, 5)
    except Exception as e:
        print(f"   ⚠️  Next button issue: {e}")

    # ── Step 2: Select Plan (Free) ──
    await human_delay(2, 3)

    try:
        # Look for "Get Started" button under Free plan
        # The Free plan has "Get Started →" button
        free_btn = page.locator(
            'button:has-text("Get Started"):first-of-type, '
            'a:has-text("Get Started"):first-of-type'
        )

        # More specific: find the Free plan section
        free_section = page.locator(
            'div:has(> :text("Free")) button:has-text("Get Started"), '
            'div:has(:text("FREE")) button:has-text("Get Started"), '
            ':text("Free") >> .. >> button:has-text("Get Started")'
        )

        if await free_section.count() > 0:
            await free_section.first.click()
            print("   ✅ Selected Free Plan")
        elif await free_btn.count() > 0:
            await free_btn.first.click()
            print("   ✅ Selected Free Plan (first Get Started)")
        else:
            # Fallback: click the first "Get Started" button
            all_get_started = page.locator('button:has-text("Get Started")')
            if await all_get_started.count() > 0:
                await all_get_started.first.click()
                print("   ✅ Clicked first Get Started button")

        await human_delay(3, 5)
    except Exception as e:
        print(f"   ⚠️  Plan selection issue: {e}")

    # ── Step 3: Add Payment Option → "Add Card Later" ──
    await human_delay(2, 3)

    try:
        # Check if "Add Payment Option" form appeared
        add_later = page.locator(
            'button:has-text("Add Card Later"), '
            'a:has-text("Add Card Later"), '
            'button:has-text("Skip"), '
            'a:has-text("Skip")'
        )
        await add_later.first.wait_for(timeout=10000)
        await human_delay(0.5, 1.5)
        await add_later.first.click()
        print("   ✅ Clicked 'Add Card Later'")
        await human_delay(3, 5)
    except Exception:
        # No payment form — maybe Free plan doesn't require it
        print("   ℹ️  No payment form (skipped)")

    # Verify we're on dashboard
    await human_delay(2, 3)
    print(f"   📍 URL: {page.url[:80]}")

    return True


# ─── Extract API Key ─────────────────────────────────────────────

async def _extract_moralis_key(page) -> str:
    """Extract API key from Moralis dashboard.

    Strategies (in order):
    1. Clipboard intercept + click copy button
    2. Click eye icon to reveal key, then read text
    3. Navigate to /api-keys page
    4. Parse page for key patterns
    """
    await human_delay(1.5, 3)
    print("   🔍 Extracting Moralis API key...")

    # Ensure we're on dashboard
    current_url = page.url
    if "onboarding" in current_url:
        await page.goto(MORALIS_DASHBOARD_URL, wait_until="domcontentloaded")
        await human_delay(3, 5)

    # ── Strategy 1: Clipboard intercept + copy button ──
    try:
        await page.evaluate("""
            window.__copiedKey = '';
            if (navigator.clipboard && navigator.clipboard.writeText) {
                const orig = navigator.clipboard.writeText.bind(navigator.clipboard);
                navigator.clipboard.writeText = async (text) => {
                    window.__copiedKey = text;
                    return orig(text);
                };
            }
        """)
    except Exception:
        pass

    # Find copy button near "API Key" text
    copy_selectors = [
        'button[aria-label*="copy" i]',
        'button[aria-label*="Copy" i]',
        'button[title*="copy" i]',
        'button[title*="Copy" i]',
        '[data-testid*="copy" i]',
        'button:has-text("Copy")',
    ]

    for selector in copy_selectors:
        try:
            btn = page.locator(selector)
            if await btn.count() > 0:
                await btn.first.click()
                await human_delay(0.5, 1.5)

                copied = await page.evaluate("window.__copiedKey")
                if copied and len(copied) > 15:
                    print(f"   ✅ Key from clipboard: {copied[:16]}...")
                    return copied.strip()

                try:
                    clip = await page.evaluate("navigator.clipboard.readText()")
                    if clip and len(clip) > 15:
                        print(f"   ✅ Key from clipboard: {clip[:16]}...")
                        return clip.strip()
                except Exception:
                    pass
                break
        except Exception:
            continue

    # Try SVG icon buttons near "API Key" area
    # Moralis dashboard: API Key row has eye icon + copy icon
    try:
        # Find buttons with SVG icons near the API Key section
        api_key_area = page.locator(':text("API Key") >> .. >> ..')
        icon_btns = api_key_area.locator('button:has(svg), button:has(img)')
        btn_count = await icon_btns.count()

        if btn_count >= 2:
            # 2nd button is usually copy
            await icon_btns.nth(1).click()
            print("   📋 Clicked copy icon near API Key")
            await human_delay(0.5, 1.5)

            copied = await page.evaluate("window.__copiedKey")
            if copied and len(copied) > 15:
                print(f"   ✅ Key from clipboard: {copied[:16]}...")
                return copied.strip()

        # Try first button (eye icon to reveal)
        if btn_count >= 1:
            await icon_btns.first.click()
            print("   👁 Clicked eye icon")
            await human_delay(0.5, 1.5)
    except Exception:
        pass

    # ── Strategy 2: Click all small icon buttons to reveal key ──
    try:
        icon_btns = page.locator('button:has(svg)')
        btn_count = await icon_btns.count()
        for idx in range(min(btn_count, 15)):
            btn = icon_btns.nth(idx)
            try:
                bbox = await btn.bounding_box()
                if bbox and bbox['width'] < 50 and bbox['height'] < 50:
                    await btn.click()
                    await human_delay(0.3, 0.5)
            except Exception:
                continue
    except Exception:
        pass

    # ── Strategy 3: Navigate to /api-keys page ──
    try:
        # Click "API Keys" in sidebar
        api_keys_link = page.locator(
            'a:has-text("API Keys"), '
            'nav a:has-text("API Keys"), '
            '[href*="api-keys"]'
        )
        if await api_keys_link.count() > 0:
            await api_keys_link.first.click()
            await human_delay(3, 5)
            print("   📄 Navigated to API Keys page")
        else:
            await page.goto(MORALIS_API_KEYS_URL, wait_until="domcontentloaded")
            await human_delay(3, 5)
    except Exception:
        try:
            await page.goto(MORALIS_API_KEYS_URL, wait_until="domcontentloaded")
            await human_delay(3, 5)
        except Exception:
            pass

    # Re-setup clipboard intercept
    try:
        await page.evaluate("""
            window.__copiedKey = '';
            if (navigator.clipboard && navigator.clipboard.writeText) {
                const orig = navigator.clipboard.writeText.bind(navigator.clipboard);
                navigator.clipboard.writeText = async (text) => {
                    window.__copiedKey = text;
                    return orig(text);
                };
            }
        """)
    except Exception:
        pass

    # Try copy buttons again on API Keys page
    for selector in copy_selectors:
        try:
            btn = page.locator(selector)
            if await btn.count() > 0:
                await btn.first.click()
                await human_delay(0.5, 1.5)

                copied = await page.evaluate("window.__copiedKey")
                if copied and len(copied) > 15:
                    print(f"   ✅ Key from API Keys page: {copied[:16]}...")
                    return copied.strip()
                break
        except Exception:
            continue

    # Click eye/reveal icons on API Keys page
    try:
        icon_btns = page.locator('button:has(svg)')
        btn_count = await icon_btns.count()
        for idx in range(min(btn_count, 15)):
            btn = icon_btns.nth(idx)
            try:
                bbox = await btn.bounding_box()
                if bbox and bbox['width'] < 50 and bbox['height'] < 50:
                    await btn.click()
                    await human_delay(0.3, 0.5)
            except Exception:
                continue
    except Exception:
        pass

    # ── Strategy 4: Parse page text for key patterns ──
    page_text = await page.inner_text("body")
    current_url = page.url

    # Moralis API keys are typically 64-char hex or long alphanumeric
    # Look for eyJhb... (JWT-like) or long hex strings
    for m in re.findall(r'eyJh[a-zA-Z0-9_-]{30,}', page_text):
        print(f"   ✅ Key (JWT): {m[:16]}...")
        return m

    for m in re.findall(r'[a-zA-Z0-9]{30,}', page_text):
        if m.startswith(("http", "data:", "function", "return", "shadow", "moralis")):
            continue
        if m in current_url:
            continue
        if any(x in m.lower() for x in [
            "classname", "style", "color", "button", "container",
            "welcome", "dashboard", "onboarding", "metamask"
        ]):
            continue
        print(f"   ✅ Key from page text: {m[:16]}...")
        return m

    # ── Strategy 5: Read input/code elements ──
    for selector in [
        'input[readonly]', 'input[type="text"]',
        'code', 'pre', '.api-key', '[class*="key"]',
        'span[class*="key"]', 'div[class*="api-key"]'
    ]:
        try:
            els = page.locator(selector)
            for idx in range(await els.count()):
                el = els.nth(idx)
                val = (
                    await el.input_value()
                    if selector.startswith("input")
                    else await el.inner_text()
                )
                val = val.strip()
                if val and len(val) > 15 and not val.startswith("http") and "•" not in val:
                    print(f"   ✅ Key from element: {val[:16]}...")
                    return val
        except Exception:
            continue

    # ── Strategy 6: Use Moralis API to get key ──
    try:
        api_key = await page.evaluate("""
            async () => {
                try {
                    // Try reading from the page's React state or global vars
                    const el = document.querySelector('[class*="api"] [class*="key"]');
                    if (el) return el.textContent.trim();

                    // Try fetching from Moralis internal API
                    const resp = await fetch('/api/keys', { credentials: 'include' });
                    if (resp.ok) {
                        const data = await resp.json();
                        if (data && data.length > 0) return data[0].key || data[0].apiKey;
                    }
                } catch(e) {}
                return '';
            }
        """)
        if api_key and len(api_key) > 15:
            print(f"   ✅ Key from JS eval: {api_key[:16]}...")
            return api_key.strip()
    except Exception:
        pass

    print("   ❌ Could not extract API key automatically")
    # Save debug artifacts for inspection
    try:
        import time as _t
        from pathlib import Path as _Path
        stamp = int(_t.time())
        debug_dir = _Path(__file__).parent / "_moralis_extract_fail"
        debug_dir.mkdir(exist_ok=True)
        await page.screenshot(path=str(debug_dir / f"{stamp}.png"), full_page=True)
        html = await page.content()
        (debug_dir / f"{stamp}.html").write_text(html[:100_000], encoding="utf-8")
        print(f"   🖼️ Moralis debug saved to _moralis_extract_fail/{stamp}.*")
    except Exception:
        pass
    return ""


# ─── Window hiding via CDP (move off-screen) ─────────────────────

async def _hide_window(ctx, page):
    """No-op — minimize/off-screen breaks Chromium rendering. Windows stay visible."""
    return


# ─── Main signup flow ────────────────────────────────────────────

async def _signup_moralis_for_account(browser, email: str, password: str) -> str:
    """
    Full Moralis signup flow for one Gmail account:
    Register → Google OAuth → onboarding → Free plan → extract key.
    Returns API key string or empty string.
    """
    ctx = await create_stealth_context(browser)
    page = await ctx.new_page()
    await _hide_window(ctx, page)

    try:
        # Step 1: Go to Moralis register
        print("   🔑 Opening Moralis register...")
        await page.goto(MORALIS_REGISTER_URL, wait_until="domcontentloaded")
        await human_delay(3, 5)

        # Step 2: Click "Login with Google" — handle both popup & same-page nav
        google_btn = page.locator(
            'button:has-text("Login with Google"), '
            'button:has-text("Sign up with Google"), '
            'button:has-text("Google"), '
            'a:has-text("Login with Google"), '
            'a:has-text("Google"), '
            '[data-testid*="google" i]'
        )
        if await google_btn.count() == 0:
            print("   ❌ Google button not found")
            return ""

        await human_delay(0.5, 1.5)
        oauth_page = page  # may swap to popup
        popup_opened = False
        try:
            async with ctx.expect_page(timeout=8000) as popup_info:
                await google_btn.first.click()
            oauth_page = await popup_info.value
            popup_opened = True
            await _hide_window(ctx, oauth_page)
            print("   🔗 Login with Google → popup")
            await oauth_page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            print("   🔗 Login with Google (same-page)")
            try:
                await page.wait_for_url(
                    lambda u: "google.com" in u, timeout=10000
                )
            except Exception:
                if "admin.moralis.com" in page.url:
                    print("   ⚠️  Click no nav → retry force")
                    try:
                        async with ctx.expect_page(timeout=8000) as popup_info:
                            await google_btn.first.click(force=True)
                        oauth_page = await popup_info.value
                        popup_opened = True
                        await _hide_window(ctx, oauth_page)
                        await oauth_page.wait_for_load_state(
                            "domcontentloaded", timeout=15000
                        )
                    except Exception:
                        try:
                            await page.wait_for_url(
                                lambda u: "google.com" in u, timeout=8000
                            )
                        except Exception:
                            print(f"   ❌ Stuck at {page.url[:60]}")
                            return ""

        await human_delay(2, 3)

        # Step 3: Google OAuth (on popup if opened, else main page)
        oauth_ok = await _google_oauth(oauth_page, email, password)
        if popup_opened:
            # Wait for popup to close OR main page to leave login
            try:
                await page.wait_for_url(
                    lambda u: "/login" not in u and "/register" not in u,
                    timeout=20000,
                )
            except Exception:
                pass
        if not oauth_ok:
            await human_delay(5, 8)
            cur = page.url
            if (
                "admin.moralis.com" not in cur
                or "/login" in cur
                or "/register" in cur
            ):
                print(f"   ❌ OAuth failed — URL: {cur[:80]}")
                return ""

        print("   ✅ Google OAuth complete → Moralis")
        await human_delay(2, 3)

        # Step 4: Check if dashboard or onboarding
        current_url = page.url

        if "onboarding" in current_url:
            # Need to complete onboarding
            await _complete_onboarding(page)
        elif "register" in current_url:
            # Still on register page — might redirect soon
            print("   ⏳ Waiting for redirect...")
            await human_delay(5, 8)
            current_url = page.url
            if "onboarding" in current_url:
                await _complete_onboarding(page)
            elif "admin.moralis.com" in current_url and "register" not in current_url:
                print("   ✅ Redirected to dashboard")
            else:
                # Try navigating to dashboard
                await page.goto(MORALIS_DASHBOARD_URL, wait_until="domcontentloaded")
                await human_delay(3, 5)
                if "onboarding" in page.url:
                    await _complete_onboarding(page)
        else:
            # On dashboard or somewhere else on moralis.com
            # Check if onboarding is needed by trying to navigate
            dashboard_text = await page.inner_text("body")
            if "Hello there" in dashboard_text or "onboarding" in page.url:
                await _complete_onboarding(page)
            else:
                print("   ✅ Already on dashboard")

        # Step 5: Extract API key
        api_key = await _extract_moralis_key(page)
        return api_key

    except Exception as e:
        print(f"   ❌ Error: {e}")
        try:
            await page.screenshot(
                path=str(DATA_DIR / f"error_moralis_{email.split('@')[0]}.png")
            )
        except Exception:
            pass
        return ""

    finally:
        await ctx.close()


# ─── Signup command ──────────────────────────────────────────────

async def signup_moralis(workers: int = 1):
    """Signup Moralis for pending accounts (not yet signed up)."""
    from playwright.async_api import async_playwright

    accounts = load_json(ACCOUNTS_FILE)
    keys = load_json(KEYS_FILE)

    pending = [a for a in accounts if not a.get("moralis_signed_up")]
    if not pending:
        print("❌ No pending accounts. Add accounts first with 'add' command.")
        return

    print(f"🔑 Signing up {len(pending)} accounts on Moralis (workers={workers})...")
    print(f"   Existing Moralis keys: {len(keys)}")
    print()

    save_lock = asyncio.Lock()
    progress = {"done": 0, "ok": 0, "fail": 0}

    async def _process(browser, idx, account):
        email = account["email"]
        password = account["password"]
        print(f"\n── [{idx + 1}/{len(pending)}] {email} ──")
        try:
            api_key = await _signup_moralis_for_account(browser, email, password)
        except Exception as e:
            print(f"   ❌ Worker error: {e}")
            api_key = ""

        async with save_lock:
            progress["done"] += 1
            if api_key:
                keys.append({
                    "email": email,
                    "api_key": api_key,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                save_json(KEYS_FILE, keys)
                account["moralis_signed_up"] = True
                save_json(ACCOUNTS_FILE, accounts)
                progress["ok"] += 1
                print(f"   ✅ [{progress['done']}/{len(pending)}] OK={progress['ok']} Fail={progress['fail']} — {api_key[:16]}...")
            else:
                progress["fail"] += 1
                print(f"   ⏭️  [{progress['done']}/{len(pending)}] OK={progress['ok']} Fail={progress['fail']} — skip")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            slow_mo=random.randint(30, 80),
            args=[
                "--window-position=-32000,-32000",
                "--window-size=1280,800",
            ],
        )

        sem = asyncio.Semaphore(max(1, workers))

        async def _runner(idx, account):
            async with sem:
                await _process(browser, idx, account)

        tasks = [asyncio.create_task(_runner(i, a)) for i, a in enumerate(pending)]
        await asyncio.gather(*tasks, return_exceptions=True)

        await browser.close()

    _print_keys(keys)
    _update_env_file(keys)


# ─── .env Auto-update ────────────────────────────────────────────

ENV_FILE = DATA_DIR.parent / ".env"


def _update_env_file(keys: list):
    """Auto-update MORALIS_API_KEYS in .env file."""
    if not keys:
        return

    all_keys = ",".join(k["api_key"] for k in keys)
    env_line = f"MORALIS_API_KEYS={all_keys}"

    if not ENV_FILE.exists():
        ENV_FILE.write_text(f"{env_line}\n")
        print(f"\n📝 Created .env with MORALIS_API_KEYS = {len(keys)} keys")
        return

    content = ENV_FILE.read_text()
    if "MORALIS_API_KEYS=" in content:
        lines = content.split("\n")
        new_lines = [
            env_line if line.startswith("MORALIS_API_KEYS=") else line
            for line in lines
        ]
        content = "\n".join(new_lines)
    else:
        content = content.rstrip("\n") + f"\n{env_line}\n"

    ENV_FILE.write_text(content)
    print(f"\n📝 Updated .env — MORALIS_API_KEYS = {len(keys)} keys")


# ─── Show Keys / Manual Add ──────────────────────────────────────

def show_keys():
    keys = load_json(KEYS_FILE)
    if not keys:
        print("❌ No Moralis keys found. Run 'signup' first.")
        return
    _print_keys(keys)
    _update_env_file(keys)


def _print_keys(keys: list):
    print(f"\n🔑 {len(keys)} Moralis API keys:")
    for k in keys:
        print(f"   {k['email']}: {k['api_key'][:16]}...")


def manual_add():
    """Manually add accounts (email|password format)."""
    accounts = load_json(ACCOUNTS_FILE)
    print("📝 Paste accounts (email|password|recovery), empty line to finish:")
    added = 0
    while True:
        line = input("> ").strip()
        if not line:
            break
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 2 and "@" in parts[0]:
            parsed = {
                "email": parts[0],
                "password": parts[1],
                "recovery": parts[2] if len(parts) > 2 else "",
                "bought_at": datetime.now(timezone.utc).isoformat(),
                "helius_signed_up": False,
                "moralis_signed_up": False,
            }
            if not any(a["email"] == parsed["email"] for a in accounts):
                accounts.append(parsed)
                added += 1
                print(f"   ✅ {parsed['email']}")
            else:
                print(f"   ⚠️  Duplicate")
        else:
            print(f"   ❌ Invalid format")
    save_json(ACCOUNTS_FILE, accounts)
    print(f"\n📊 Added {added}. Total: {len(accounts)}")


# ─── CLI ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Moralis API Key Farmer")
    sub = parser.add_subparsers(dest="command")

    signup_p = sub.add_parser("signup", help="Signup Moralis for pending accounts")
    signup_p.add_argument("-w", "--workers", type=int, default=1, help="Parallel workers (default 1)")
    sub.add_parser("keys", help="Show collected API keys")
    sub.add_parser("add", help="Manually add accounts")

    args = parser.parse_args()

    if args.command == "signup":
        asyncio.run(signup_moralis(workers=args.workers))
    elif args.command == "keys":
        show_keys()
    elif args.command == "add":
        manual_add()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
