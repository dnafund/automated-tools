"""
Helius API Key Farmer
Automates: buy Gmail on mail72h.com → signup Helius via Google OAuth → extract API key.

Flow (verified from video):
  1. mail72h.com homepage → dismiss popup "Không hiển thị lại trong 2 giờ"
  2. /client/login → fill username/password → click "ĐĂNG NHẬP"
  3. /product/29 → click "MUA NGAY" → modal → "THANH TOÁN"
  4. Success modal shows: "email |password" → parse + save
  5. Helius /signup → click "Google" → Google Sign in (email → pass → "I understand" → "Continue")
  6. Helius auto-creates project → /api-keys page → click 👁 show key → extract

Requirements:
    pip install playwright
    playwright install chromium

Usage:
    python3 helius_key_farmer.py auto --count 10
    python3 helius_key_farmer.py buy --count 10
    python3 helius_key_farmer.py signup
    python3 helius_key_farmer.py keys
    python3 helius_key_farmer.py add

Files:
    tools/accounts.json    — bought Gmail accounts [{email, password, recovery}]
    tools/helius_keys.json — Helius API keys [{email, api_key}]
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
from playwright_stealth import Stealth

DATA_DIR = Path(__file__).parent
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
KEYS_FILE = DATA_DIR / "helius_keys.json"

HELIUS_SIGNUP_URL = "https://dashboard.helius.dev/signup"
HELIUS_API_KEYS_URL = "https://dashboard.helius.dev/api-keys"
MAIL72H_URL = "https://mail72h.com"

# mail72h.com credentials
MAIL72H_USER = os.getenv("MAIL72H_USER", "datthieu")
MAIL72H_PASS = os.getenv("MAIL72H_PASS", "Datthieuls123")


# ─── Anti-detect helpers ─────────────────────────────────────────

async def human_delay(min_s: float = 0.5, max_s: float = 2.0):
    """Random delay to mimic human behavior."""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def human_type(page_or_locator, text: str, min_delay=50, max_delay=150):
    """Type text character by character with random delay (ms)."""
    for char in text:
        await page_or_locator.press(char)
        await asyncio.sleep(random.uniform(min_delay, max_delay) / 1000)


STEALTH = Stealth(
    navigator_languages_override=("vi-VN", "vi"),
    navigator_platform_override="MacIntel",
)


async def create_stealth_context(browser):
    """Create a browser context with anti-detect settings."""
    width = random.randint(1280, 1440)
    height = random.randint(800, 900)

    context = await browser.new_context(
        viewport={"width": width, "height": height},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        locale="vi-VN",
        timezone_id="Asia/Ho_Chi_Minh",
        color_scheme="dark",
        permissions=["clipboard-read", "clipboard-write"],
    )

    # Apply playwright-stealth evasions
    await STEALTH.apply_stealth_async(context)

    return context


def load_json(path: Path) -> list:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


def save_json(path: Path, data: list) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ─── mail72h helpers ─────────────────────────────────────────────

async def _dismiss_mail72h_popup(page):
    """Dismiss popup on mail72h.com. Priority: 'Không hiển thị' first."""
    try:
        await human_delay(1, 2.5)

        # Priority 1: "Không hiển thị lại trong 2 giờ" (red button bottom-right)
        dismiss_btn = page.locator(
            'button:has-text("Không hiển thị"), '
            'a:has-text("Không hiển thị"), '
            'button:has-text("Tạm ẩn"), '
            'a:has-text("Tạm ẩn"), '
            'span:has-text("Không hiển thị"), '
            'span:has-text("Tạm ẩn")'
        )
        if await dismiss_btn.count() > 0:
            await dismiss_btn.first.click()
            print("   ✅ Đã tắt popup")
            await human_delay(0.5, 1.5)
            return

        # Priority 2: X button (top-right of "Thông báo" popup)
        close_btn = page.locator(
            'button.close, .modal .close, '
            '[aria-label="Close"], '
            'button:has-text("×"), '
            '.modal-header button'
        )
        if await close_btn.count() > 0:
            await close_btn.first.click()
            print("   ✅ Đã tắt popup (X)")
            await human_delay(0.3, 0.8)
            return

        # Priority 3: Click backdrop
        modal_backdrop = page.locator('.modal-backdrop, .overlay')
        if await modal_backdrop.count() > 0:
            await modal_backdrop.first.click()
            await human_delay(0.3, 0.8)

    except Exception:
        pass


def _setup_auto_dismiss_popup(page):
    """Register a background listener that auto-dismisses popups whenever they appear."""

    async def _on_popup_check():
        """Periodically check and close popups."""
        while True:
            try:
                dismiss_btn = page.locator(
                    'button:has-text("Không hiển thị"):visible, '
                    'a:has-text("Không hiển thị"):visible'
                )
                if await dismiss_btn.count() > 0:
                    await dismiss_btn.first.click()
                    print("   🔕 Auto-tắt popup")
                    await asyncio.sleep(1)
                    continue

                close_btn = page.locator('button.close:visible, button:has-text("×"):visible')
                # Only close if "Thông báo" text is visible (avoid closing buy modals)
                thongbao = page.locator('text="Thông báo"')
                if await thongbao.count() > 0 and await close_btn.count() > 0:
                    await close_btn.first.click()
                    print("   🔕 Auto-tắt popup (X)")
                    await asyncio.sleep(0.5)
            except Exception:
                pass
            await asyncio.sleep(3)  # Check every 3 seconds

    # Start background task
    return asyncio.create_task(_on_popup_check())


async def _login_mail72h(page) -> bool:
    """Dismiss popup on homepage FIRST, then login at /client/login."""
    try:
        # Step 1: Homepage → dismiss popup
        print("   🔔 Tắt popup trước...")
        await page.goto(MAIL72H_URL, wait_until="networkidle")
        await human_delay(2, 4)
        await _dismiss_mail72h_popup(page)

        # Step 2: Login page (correct URL: /client/login)
        print("   🔐 Đăng nhập...")
        await page.goto(f"{MAIL72H_URL}/client/login", wait_until="networkidle")
        await human_delay(1, 2)
        await _dismiss_mail72h_popup(page)

        # Fill username — type like a human
        user_input = page.locator('input[type="text"], input[name="username"], input[name="email"]').first
        await user_input.click()
        await human_delay(0.3, 0.6)
        await human_type(user_input, MAIL72H_USER)

        await human_delay(0.5, 1)

        # Fill password
        pw_input = page.locator('input[type="password"]').first
        await pw_input.click()
        await human_delay(0.3, 0.6)
        await human_type(pw_input, MAIL72H_PASS)

        await human_delay(0.5, 1)

        # Click "ĐĂNG NHẬP"
        login_btn = page.locator('button:has-text("ĐĂNG NHẬP"), button:has-text("Đăng nhập"), button[type="submit"]')
        await login_btn.first.click()
        await human_delay(3, 5)
        await page.wait_for_load_state("networkidle")

        # Verify login
        page_text = await page.inner_text("body")
        if "DATTHIEU" in page_text or "Số dư" in page_text or "datthieu" in page_text.lower():
            print("   ✅ mail72h.com login OK")
            return True

        print("   ⚠️  mail72h login unclear — check browser")
        return True

    except Exception as e:
        print(f"   ❌ mail72h login error: {e}")
        return False


async def _buy_one_gmail(page) -> dict | None:
    """Buy 1 Gmail from product/29. Returns parsed account or None."""

    await page.goto(f"{MAIL72H_URL}/product/29", wait_until="domcontentloaded")
    await human_delay(1.5, 3)
    await _dismiss_mail72h_popup(page)

    # Click "MUA NGAY" green button → opens modal
    mua_btn = page.locator('button:has-text("MUA NGAY"), a:has-text("MUA NGAY")')
    if await mua_btn.count() > 0:
        await human_delay(0.5, 1.2)
        await mua_btn.first.click()
        await human_delay(1.5, 3)
        print("   📦 Đã mở modal mua hàng")
    else:
        try:
            await page.evaluate("openModal(29)")
            await human_delay(1.5, 3)
            print("   📦 Mở modal qua JS")
        except Exception:
            print("   ❌ Không mở được modal")
            return None

    # Click "THANH TOÁN" inside modal (qty defaults to 1)
    await human_delay(0.5, 1.5)
    buy_btn = page.locator('button:has-text("THANH TOÁN"):visible')
    if await buy_btn.count() > 0:
        await buy_btn.first.click()
        print("   💳 Đã click THANH TOÁN")
        await human_delay(3, 5)
    else:
        print("   ❌ Không tìm thấy nút THANH TOÁN")
        return None

    await page.wait_for_load_state("domcontentloaded")
    await human_delay(1, 2.5)

    # Success modal shows "Thanh toán thành công!"
    # Account text is in the modal body, format: "email |password"
    # Try modal content first, then page body
    account_text = ""

    # Look for the success modal content
    modal_body = page.locator('.modal-body, .swal2-html-container, [class*="modal"] [class*="body"]')
    if await modal_body.count() > 0:
        account_text = await modal_body.first.inner_text()

    if not account_text or "@" not in account_text:
        for selector in ["textarea", "pre", "code"]:
            el = page.locator(selector)
            if await el.count() > 0:
                account_text = await el.first.input_value() if selector == "textarea" else await el.first.inner_text()
                if account_text and "@" in account_text:
                    break

    if not account_text or "@" not in account_text:
        account_text = await page.inner_text("body")

    parsed = _parse_account(account_text)

    # Close success modal — try specific buttons first, skip if fails
    try:
        # Priority 1: "Mua thêm" button (most reliable)
        mua_them = page.locator('button:has-text("Mua thêm"):visible')
        if await mua_them.count() > 0:
            await mua_them.first.click(timeout=3000)
            await human_delay(0.5, 1.5)
        else:
            # Priority 2: Swal2 confirm button or modal close X
            swal_btn = page.locator('.swal2-confirm:visible, .swal2-close:visible')
            if await swal_btn.count() > 0:
                await swal_btn.first.click(timeout=3000)
                await human_delay(0.5, 1)
            else:
                # Priority 3: Press Escape to dismiss any modal
                await page.keyboard.press("Escape")
                await human_delay(0.3, 0.8)
    except Exception:
        # Not critical — we already have the account data
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass

    return parsed


def _parse_account(text: str) -> dict | None:
    """Parse email|password or email |password from text."""
    lines = [l.strip() for l in text.split("\n") if "|" in l or "@" in l]
    for line in lines:
        # Handle "email |password" (space before pipe) from mail72h
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 2 and "@" in parts[0]:
            return {
                "email": parts[0],
                "password": parts[1],
                "recovery": parts[2] if len(parts) > 2 else "",
                "bought_at": datetime.now(timezone.utc).isoformat(),
                "helius_signed_up": False,
            }
    return None


# ─── Helius helpers ──────────────────────────────────────────────

async def _signup_helius_for_account(browser, email: str, password: str) -> str:
    """
    Full Helius signup flow for one Gmail account:
    Helius /signup → Google → email/pass → consent → project creation → extract key.
    Returns API key string or empty string.
    """
    ctx = await create_stealth_context(browser)
    page = await ctx.new_page()

    try:
        # Step 1: Go to Helius signup
        print("   🔑 Mở Helius signup...")
        await page.goto(HELIUS_SIGNUP_URL, wait_until="networkidle")
        await human_delay(2, 4)

        # Step 2: Click "Google" button
        google_btn = page.locator(
            'button:has-text("Google"), '
            'a:has-text("Google")'
        )
        if await google_btn.count() > 0:
            await human_delay(0.5, 1.5)
            await google_btn.first.click()
            print("   🔗 Clicked Google...")
        else:
            if sys.stdin.isatty():
                print("   ❌ Không thấy nút Google → click tay, rồi Enter:")
                input("   > ")
            else:
                raise Exception("No Google button")

        await human_delay(2, 4)

        # Step 3: Google Sign in — email
        # Google uses #identifierId, input[type="email"], or may pre-fill from OAuth
        email_input = page.locator('input#identifierId, input[type="email"], input[name="identifier"]').first
        try:
            await email_input.wait_for(timeout=10000)
            await human_delay(0.5, 1)

            # Check if email already pre-filled by OAuth
            current_val = await email_input.input_value()
            if current_val and "@" in current_val:
                print(f"   📧 Email đã tự fill: {current_val}")
            else:
                await email_input.click()
                await human_delay(0.3, 0.6)
                await human_type(email_input, email)
                print(f"   📧 Nhập email: {email}")

            await human_delay(0.5, 1)
            next_btn = page.locator('#identifierNext, button:has-text("Next"), button:has-text("Tiếp theo")')
            await next_btn.first.click()
            await human_delay(2, 4)
        except Exception:
            # Maybe email page was skipped (OAuth pre-auth) — try clicking Next anyway
            try:
                next_btn = page.locator('#identifierNext, button:has-text("Tiếp theo"), button:has-text("Next")')
                if await next_btn.count() > 0:
                    await next_btn.first.click()
                    print(f"   📧 Clicked Next (email pre-filled)")
                    await human_delay(2, 4)
                else:
                    if sys.stdin.isatty():
                        print("   ⚠️  Email step unclear → xử lý tay, Enter khi xong:")
                        input("   > ")
                    else:
                        raise Exception("Email step unclear")
            except Exception as e:
                if "Email step" in str(e):
                    raise
                if sys.stdin.isatty():
                    print("   ⚠️  Email input not found → xử lý tay, Enter khi xong:")
                    input("   > ")
                else:
                    raise Exception("Email input not found")

        # Step 4: Google Sign in — password
        pw_input = page.locator('input[type="password"]:visible, input[name="Passwd"]:visible').first
        try:
            await pw_input.wait_for(timeout=15000)
            await human_delay(0.5, 1)
            await pw_input.click()
            await human_delay(0.3, 0.6)
            await human_type(pw_input, password)
            await human_delay(0.5, 1)
            next_btn = page.locator('#passwordNext, button:has-text("Next"), button:has-text("Tiếp theo")')
            await next_btn.first.click()
            print("   🔑 Nhập password")
            await human_delay(3, 6)
        except Exception:
            if sys.stdin.isatty():
                print("   ⚠️  Password field issue → xử lý tay, Enter khi xong:")
                input("   > ")
            else:
                raise Exception("Password field not found")

        # Step 5: Speedbump "Tôi hiểu" — bypass scroll bằng JS
        await human_delay(2, 3)
        if "speedbump" in page.url or "gaplustos" in page.url:
            print("   📜 Speedbump → force click via JS...")
            await page.evaluate("""
                const btns = [...document.querySelectorAll('button, input[type="button"], input[type="submit"]')];
                const btn = btns.find(b => {
                    const t = (b.textContent || b.value || '').toLowerCase();
                    return t.includes('hiểu') || t.includes('understand');
                });
                if (btn) {
                    btn.disabled = false;
                    btn.removeAttribute('disabled');
                    btn.click();
                }
            """)
            print("   ✅ Clicked 'Tôi hiểu'")
            await human_delay(3, 5)

        # Step 6: OAuth consent — "Continue" / "Cho phép"
        try:
            consent = page.locator('button:has-text("Continue"), button:has-text("Cho phép"), #submit_approve_access')
            await consent.first.wait_for(timeout=10000)
            await human_delay(1, 2)
            await consent.first.click()
            print("   ✅ OAuth consent")
            await human_delay(3, 6)
        except Exception:
            pass

        # Step 7: Complete onboarding + go to API Keys page
        await human_delay(3, 5)
        print(f"   📍 URL: {page.url[:80]}")

        # 7a: Handle onboarding form (new Helius flow requires this before /api-keys)
        if "onboarding" in page.url:
            print("   📝 Onboarding form detected, filling...")
            for _ in range(5):  # up to 5 onboarding steps
                # Fill any visible text input (name / company / project)
                try:
                    inputs = page.locator('input[type="text"]:visible, input:not([type]):visible')
                    for i in range(await inputs.count()):
                        try:
                            inp = inputs.nth(i)
                            val = await inp.input_value()
                            if not val:
                                await inp.fill("MyProject")
                                await human_delay(0.3, 0.8)
                        except Exception:
                            pass
                except Exception:
                    pass
                # Click first visible "Next"/"Continue"/"Submit"/"Create"/"Done"/"Skip" button
                next_btns = page.locator(
                    'button:has-text("Next"), button:has-text("Continue"), '
                    'button:has-text("Create"), button:has-text("Submit"), '
                    'button:has-text("Done"), button:has-text("Skip"), '
                    'button:has-text("Get started"), button[type="submit"]'
                )
                if await next_btns.count() > 0:
                    try:
                        await next_btns.first.click()
                        print(f"   ➡️ Onboarding step (url={page.url[-30:]})")
                        await human_delay(2, 4)
                    except Exception:
                        break
                else:
                    break
                if "onboarding" not in page.url:
                    break

        # 7b: Escape onboarding → go to dashboard to check for empty state
        if "onboarding" in page.url:
            try:
                await page.goto("https://dashboard.helius.dev/dashboard", wait_until="domcontentloaded")
                await human_delay(3, 5)
                print(f"   🔗 Nav to /dashboard (url={page.url[-40:]})")
            except Exception:
                pass

        # 7c: Handle empty dashboard — click "Get Started" to create first project.
        # Helius new flow: signup → /dashboard with "Create Your First Project" → click
        # Get Started → project creation form/modal → then /api-keys has a key.
        for attempt in range(3):
            try:
                await human_delay(2, 3)
                create_project = page.locator(
                    'button:visible:has-text("Get Started"), '
                    'button:visible:has-text("Get started"), '
                    'button:visible:has-text("Create Your First Project"), '
                    'button:visible:has-text("Create project"), '
                    'button:visible:has-text("Create Project")'
                )
                btn_count = await create_project.count()
                print(f"   🔎 Get Started buttons visible: {btn_count}")
                if btn_count == 0:
                    break
                print(f"   📦 Clicking Get Started (attempt {attempt+1})")
                # Use force + scroll_into_view for reliability
                await create_project.first.scroll_into_view_if_needed()
                await create_project.first.click(force=True)
                await human_delay(4, 7)
                print(f"   📍 After click: {page.url[-50:]}")

                # Screenshot after click to verify state transition
                try:
                    import time as _t
                    _dbg = DATA_DIR / "_extract_fail"
                    _dbg.mkdir(exist_ok=True)
                    await page.screenshot(
                        path=str(_dbg / f"post_getstarted_{int(_t.time())}.png"),
                        full_page=True,
                    )
                except Exception:
                    pass

                # Fill any visible text input (name/description)
                try:
                    inputs = page.locator('input[type="text"]:visible, input:not([type]):visible')
                    for i in range(await inputs.count()):
                        inp = inputs.nth(i)
                        val = await inp.input_value()
                        if not val:
                            await inp.fill(f"Scanner{random.randint(100, 999)}")
                            await human_delay(0.3, 0.8)
                            print(f"   📝 Filled input with Scanner")
                except Exception:
                    pass

                # Click "Start building" (Free plan) if on plan selection page
                start_free = page.locator('button:visible:has-text("Start building")')
                sfb_count = await start_free.count()
                if sfb_count > 0:
                    try:
                        await start_free.first.scroll_into_view_if_needed()
                        await start_free.first.click(force=True)
                        print(f"   🆓 Start building (Free plan) clicked")
                        await human_delay(5, 8)
                        print(f"   📍 After Free plan: {page.url[-50:]}")
                        # Screenshot after plan selection
                        try:
                            import time as _t
                            _dbg = DATA_DIR / "_extract_fail"
                            await page.screenshot(
                                path=str(_dbg / f"post_freeplan_{int(_t.time())}.png"),
                                full_page=True,
                            )
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"   Start building err: {e}")
                    continue  # re-check for more steps

                # Click generic Create/Submit/Continue
                submit_btn = page.locator(
                    'button:visible:has-text("Create"), '
                    'button:visible:has-text("Submit"), '
                    'button:visible:has-text("Continue"), '
                    'button:visible:has-text("Next"), '
                    'button:visible[type="submit"]'
                )
                sub_count = await submit_btn.count()
                print(f"   🔎 Submit buttons visible: {sub_count}")
                if sub_count > 0:
                    try:
                        await submit_btn.first.click(force=True)
                        print("   ✅ Submit clicked")
                        await human_delay(5, 8)
                    except Exception as e:
                        print(f"   Submit click err: {e}")
            except Exception as e:
                print(f"   Get Started loop err: {e}")
                break

        # 7d: Force navigate to /api-keys
        if "/api-keys" not in page.url:
            try:
                await page.goto(HELIUS_API_KEYS_URL, wait_until="domcontentloaded")
                print(f"   🔗 Force nav to /api-keys (url={page.url[-40:]})")
                await human_delay(3, 5)
            except Exception:
                pass

        # 8b: Nếu chưa có key → click "Create new"
        try:
            no_keys = page.locator('text=/No API keys found/i')
            await no_keys.wait_for(timeout=5000)
            print("   📭 No keys → Create new...")
            create_btn = page.locator('button:has-text("Create new"), button:has-text("Create New")')
            await create_btn.first.click()
            await human_delay(3, 5)
            # Confirm modal nếu có
            try:
                ok = page.locator('button:has-text("Create"), button:has-text("Generate"), button[type="submit"]')
                await ok.first.wait_for(timeout=5000)
                await ok.first.click()
                await human_delay(3, 5)
            except Exception:
                pass
            await page.goto(HELIUS_API_KEYS_URL, wait_until="domcontentloaded")
            await human_delay(3, 5)
        except Exception:
            pass

        # Step 9: Extract API key
        api_key = await _extract_api_key(page)
        return api_key

    except Exception as e:
        print(f"   ❌ Error: {e}")
        try:
            await page.screenshot(path=str(DATA_DIR / f"error_{email.split('@')[0]}.png"))
        except Exception:
            pass
        return ""

    finally:
        await ctx.close()


async def _extract_api_key(page) -> str:
    """Extract API key from Helius /api-keys page.

    Strategies (in order):
    1. Network response intercept (/api/keys XHR — most reliable)
    2. Intercept clipboard + click copy button
    3. Click eye icon to reveal key, then read text
    4. Parse page for key patterns (UUID, long alphanumeric)
    5. Read input/code elements
    """
    await human_delay(1.5, 3)
    print("   🔍 Extracting API key...")

    # ── Strategy 0: Network intercept — capture /api/keys or /api/projects response ──
    # Helius dashboard fetches keys via XHR; key appears plaintext in JSON response.
    try:
        captured_key = {"value": ""}

        async def _on_response(response):
            try:
                url = response.url
                if any(p in url for p in ["/api/keys", "/api/projects", "/apiKeys", "/keys"]):
                    if response.status == 200:
                        try:
                            data = await response.json()
                        except Exception:
                            return
                        # Walk JSON for UUID pattern values
                        import json as _json
                        text = _json.dumps(data)
                        for m in re.findall(
                            r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}',
                            text,
                        ):
                            if m not in page.url:
                                captured_key["value"] = m
                                break
                        # Also check for "apiKey" / "api_key" field
                        if not captured_key["value"]:
                            for key_field in ("apiKey", "api_key", "key"):
                                if key_field in text:
                                    m2 = re.search(
                                        rf'"{key_field}"\s*:\s*"([^"]+)"', text
                                    )
                                    if m2 and len(m2.group(1)) > 15:
                                        captured_key["value"] = m2.group(1)
                                        break
            except Exception:
                pass

        page.on("response", _on_response)

        # Trigger refetch by reloading page
        await page.reload(wait_until="networkidle")
        await human_delay(2, 4)

        if captured_key["value"]:
            print(f"   ✅ Key from network: {captured_key['value'][:16]}...")
            page.remove_listener("response", _on_response)
            return captured_key["value"]
        page.remove_listener("response", _on_response)
    except Exception as e:
        print(f"   (network intercept failed: {e})")

    # ── Strategy 1: Clipboard intercept + copy button ──
    # Monkey-patch clipboard.writeText to capture copied value
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

    # Find copy buttons — try multiple selectors
    copy_selectors = [
        'button[aria-label*="copy" i]',
        'button[aria-label*="Copy" i]',
        'button[title*="copy" i]',
        'button[title*="Copy" i]',
        'button[aria-label*="API Key" i]',
        '[data-testid*="copy" i]',
        'button:has-text("Copy")',
    ]
    clicked_copy = False
    for selector in copy_selectors:
        try:
            btn = page.locator(selector)
            if await btn.count() > 0:
                await btn.first.click()
                clicked_copy = True
                print("   📋 Clicked copy button")
                await human_delay(0.5, 1.5)

                # Read intercepted value
                copied = await page.evaluate("window.__copiedKey")
                if copied and len(copied) > 15:
                    print(f"   ✅ Key from clipboard: {copied[:16]}...")
                    return copied.strip()

                # Fallback: read clipboard directly
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

    # If no labeled copy button found, try the 2nd small SVG icon button in key row
    # (Helius layout: eye icon = show, clipboard icon = copy)
    if not clicked_copy:
        try:
            key_row_btns = page.locator('tr button:has(svg), td button:has(svg), [class*="key"] button:has(svg)')
            btn_count = await key_row_btns.count()
            if btn_count >= 2:
                # 2nd icon button = copy
                await key_row_btns.nth(1).click()
                print("   📋 Clicked 2nd icon (copy)")
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
        except Exception:
            pass

    # ── Strategy 2: Click eye/show icon to reveal hidden key ──
    icon_btns = page.locator('button:has(svg)')
    btn_count = await icon_btns.count()
    for idx in range(min(btn_count, 10)):
        btn = icon_btns.nth(idx)
        try:
            bbox = await btn.bounding_box()
            if bbox and bbox['width'] < 50 and bbox['height'] < 50:
                await human_delay(0.3, 0.8)
                await btn.click()
                await human_delay(0.5, 1)
        except Exception:
            continue

    # ── Strategy 3: Parse page text for key patterns ──
    page_text = await page.inner_text("body")
    current_url = page.url

    # Try alphanumeric key (Helius keys are typically 30+ chars)
    for m in re.findall(r'[a-zA-Z0-9_-]{30,}', page_text):
        # Skip common false positives
        if m.startswith(("http", "data:", "function", "return", "project", "shadow")):
            continue
        if m in current_url:
            continue
        # Skip CSS class names and common JS tokens
        if any(x in m.lower() for x in ["classname", "style", "color", "button", "container"]):
            continue
        print(f"   ✅ Key from page text: {m[:16]}...")
        return m

    # Try UUID pattern (some Helius keys use UUIDs)
    uuid_pattern = r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'
    for m in re.findall(uuid_pattern, page_text):
        if m not in current_url:
            print(f"   ✅ Key (UUID): {m[:16]}...")
            return m

    # ── Strategy 4: Read input elements and code blocks ──
    for selector in ['input[readonly]', 'input[type="text"]', 'code', 'pre', '.api-key', '[class*="key"]']:
        try:
            els = page.locator(selector)
            for idx in range(await els.count()):
                el = els.nth(idx)
                val = await el.input_value() if selector.startswith("input") else await el.inner_text()
                val = val.strip()
                if val and len(val) > 15 and not val.startswith("http") and "•" not in val:
                    print(f"   ✅ Key from element: {val[:16]}...")
                    return val
        except Exception:
            continue

    print("   ❌ Không tìm thấy key tự động")
    # Save debug artifacts for inspection
    try:
        import time as _t
        stamp = int(_t.time())
        debug_dir = DATA_DIR / "_extract_fail"
        debug_dir.mkdir(exist_ok=True)
        await page.screenshot(path=str(debug_dir / f"{stamp}_{page.url[-30:].replace('/','_')}.png"), full_page=True)
        html = await page.content()
        (debug_dir / f"{stamp}.html").write_text(html[:100_000], encoding="utf-8")
        print(f"   🖼️ Debug saved to _extract_fail/{stamp}_*")
    except Exception:
        pass
    return ""


# ─── Full Auto Pipeline ─────────────────────────────────────────

async def auto_pipeline(count: int = 10):
    """Full auto: buy Gmail → signup Helius → extract key."""
    from playwright.async_api import async_playwright

    accounts = load_json(ACCOUNTS_FILE)
    keys = load_json(KEYS_FILE)

    print(f"🚀 Auto pipeline: {count} Helius API keys")
    print(f"   Existing accounts: {len(accounts)} | keys: {len(keys)}")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=random.randint(30, 80))

        # ─── Phase 1: Buy Gmail accounts ─────────────────────
        print("=" * 50)
        print("📧 PHASE 1: Mua Gmail từ mail72h.com")
        print("=" * 50)

        mail_context = await create_stealth_context(browser)
        cookies_file = DATA_DIR / "mail72h_cookies.json"
        if cookies_file.exists():
            try:
                await mail_context.add_cookies(load_json(cookies_file))
            except Exception:
                pass

        mail_page = await mail_context.new_page()
        new_accounts = []

        # Start auto-dismiss popup background task
        popup_task = _setup_auto_dismiss_popup(mail_page)

        print("   🔐 Login mail72h.com...")
        await _login_mail72h(mail_page)

        for i in range(count):
            print(f"\n── Mua Gmail {i + 1}/{count} ──")

            parsed = await _buy_one_gmail(mail_page)

            if parsed:
                if not any(a["email"] == parsed["email"] for a in accounts):
                    accounts.append(parsed)
                    new_accounts.append(parsed)
                    save_json(ACCOUNTS_FILE, accounts)
                    print(f"   ✅ {parsed['email']}")
                else:
                    print(f"   ⚠️  Duplicate: {parsed['email']}")
            else:
                print("   ❌ Không parse được. Paste thủ công (email|password):")
                manual = input("   > ").strip()
                parsed = _parse_account(manual)
                if parsed and not any(a["email"] == parsed["email"] for a in accounts):
                    accounts.append(parsed)
                    new_accounts.append(parsed)
                    save_json(ACCOUNTS_FILE, accounts)
                    print(f"   ✅ {parsed['email']}")

            # Random delay between purchases to avoid detection
            if i < count - 1:
                delay = random.uniform(3, 8)
                print(f"   ⏳ Chờ {delay:.0f}s...")
                await asyncio.sleep(delay)

            await human_delay(0.3, 1)

        # Stop auto-dismiss popup task
        popup_task.cancel()

        # Save cookies
        try:
            mail_cookies = await mail_context.cookies()
            save_json(cookies_file, mail_cookies)
        except Exception:
            pass
        await mail_context.close()

        print(f"\n📊 Đã mua {len(new_accounts)} Gmail mới")

        if not new_accounts:
            new_accounts = [a for a in accounts if not a.get("helius_signed_up")]

        if not new_accounts:
            print("❌ Không có account nào để signup Helius")
            await browser.close()
            return

        # ─── Phase 2: Signup Helius ──────────────────────────
        print()
        print("=" * 50)
        print("🔑 PHASE 2: Signup Helius (Google OAuth → API key)")
        print("=" * 50)

        success_count = 0

        for i, account in enumerate(new_accounts):
            email = account["email"]
            password = account["password"]
            print(f"\n── [{i + 1}/{len(new_accounts)}] {email} ──")

            api_key = await _signup_helius_for_account(browser, email, password)

            if api_key:
                keys.append({
                    "email": email,
                    "api_key": api_key,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                save_json(KEYS_FILE, keys)
                account["helius_signed_up"] = True
                save_json(ACCOUNTS_FILE, accounts)
                success_count += 1
                print(f"   ✅ API Key: {api_key[:16]}...")
            else:
                if sys.stdin.isatty():
                    print("   ⚠️  Không lấy được key tự động")
                    print("   → Copy API key từ browser, paste vào đây:")
                    manual_key = input("   > ").strip()
                    if manual_key:
                        keys.append({
                            "email": email,
                            "api_key": manual_key,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        })
                        save_json(KEYS_FILE, keys)
                        account["helius_signed_up"] = True
                        save_json(ACCOUNTS_FILE, accounts)
                        success_count += 1
                        print(f"   ✅ Saved")
                else:
                    print("   ⏭️  Skip (non-interactive) — retry later")

        await browser.close()

    # ─── Phase 3: Results ─────────────────────────────────
    print()
    print("=" * 50)
    print("📊 KẾT QUẢ")
    print("=" * 50)
    print(f"   Gmail đã mua: {len(new_accounts)}")
    print(f"   Helius keys lấy được: {success_count}")
    print(f"   Tổng keys: {len(keys)}")

    if keys:
        _print_keys(keys)
        _update_env_file(keys)


# ─── Buy Gmail (standalone) ─────────────────────────────────────

async def buy_gmail(count: int = 10):
    """Buy Gmail edu accounts from mail72h.com."""
    from playwright.async_api import async_playwright

    accounts = load_json(ACCOUNTS_FILE)
    print(f"📧 Buying {count} Gmail accounts from mail72h.com...")
    print(f"   Existing accounts: {len(accounts)}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=random.randint(30, 80))
        context = await create_stealth_context(browser)

        cookies_file = DATA_DIR / "mail72h_cookies.json"
        if cookies_file.exists():
            try:
                await context.add_cookies(load_json(cookies_file))
            except Exception:
                pass

        page = await context.new_page()
        popup_task = _setup_auto_dismiss_popup(page)

        print("   🔐 Login mail72h.com...")
        await _login_mail72h(page)

        for i in range(count):
            print(f"\n── Account {i + 1}/{count} ──")

            parsed = await _buy_one_gmail(page)

            if parsed:
                if not any(a["email"] == parsed["email"] for a in accounts):
                    accounts.append(parsed)
                    save_json(ACCOUNTS_FILE, accounts)
                    print(f"   ✅ {parsed['email']}")
                else:
                    print(f"   ⚠️  Duplicate: {parsed['email']}")
            else:
                print("   ❌ Paste manually (email|password):")
                manual = input("   > ").strip()
                parsed = _parse_account(manual)
                if parsed:
                    accounts.append(parsed)
                    save_json(ACCOUNTS_FILE, accounts)
                    print(f"   ✅ Saved: {parsed['email']}")

            # Random delay between purchases
            if i < count - 1:
                delay = random.uniform(3, 8)
                print(f"   ⏳ Chờ {delay:.0f}s...")
                await asyncio.sleep(delay)

        popup_task.cancel()
        try:
            cookies = await context.cookies()
            save_json(cookies_file, cookies)
        except Exception:
            pass
        await browser.close()

    print(f"\n📊 Total accounts: {len(accounts)}")


# ─── Signup Helius (standalone) ──────────────────────────────────

async def signup_helius(workers: int = 1):
    """Signup Helius for pending accounts. workers > 1 = parallel Chrome instances."""
    from playwright.async_api import async_playwright
    import threading

    accounts = load_json(ACCOUNTS_FILE)
    keys = load_json(KEYS_FILE)

    pending = [a for a in accounts if not a.get("helius_signed_up")]
    if not pending:
        print("❌ No pending accounts. Run 'buy' first or 'add' accounts.")
        return

    workers = min(workers, len(pending))
    print(f"🔑 Signing up {len(pending)} accounts on Helius ({workers} workers)...")

    # Thread-safe file writes
    _lock = threading.Lock()
    _ok = [0]
    _fail = [0]

    def _save_result(account, api_key):
        with _lock:
            if api_key:
                # Load fresh from file to avoid overwriting keys from other batches
                fresh_keys = load_json(KEYS_FILE)
                fresh_keys.append({
                    "email": account["email"],
                    "api_key": api_key,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                save_json(KEYS_FILE, fresh_keys)
                keys.append({"email": account["email"], "api_key": api_key})  # keep in-memory for stats
                account["helius_signed_up"] = True
                _ok[0] += 1
            else:
                account["helius_signed_up"] = "failed"
                _fail[0] += 1
            # Load fresh accounts too
            fresh_accounts = load_json(ACCOUNTS_FILE)
            for fa in fresh_accounts:
                if fa["email"] == account["email"]:
                    fa["helius_signed_up"] = account["helius_signed_up"]
                    break
            save_json(ACCOUNTS_FILE, fresh_accounts)

    sem = asyncio.Semaphore(workers)
    total = len(pending)

    async def _process_one(idx, account, p):
        async with sem:
            email = account["email"]
            password = account["password"]
            done = _ok[0] + _fail[0]
            print(f"  [{done+1}/{total}] {email} (w={workers})")

            # Each worker: fresh browser, fresh context — no session leak
            browser = await p.chromium.launch(
                headless=True,
                slow_mo=random.randint(20, 50),
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            try:
                api_key = await _signup_helius_for_account(browser, email, password)
                _save_result(account, api_key)

                if api_key:
                    print(f"   ✅ {api_key[:16]}... (OK:{_ok[0]} Fail:{_fail[0]})")
                else:
                    print(f"   ⏭️  Skip (OK:{_ok[0]} Fail:{_fail[0]})")
            finally:
                await browser.close()

    async with async_playwright() as p:
        tasks = [
            _process_one(i, account, p)
            for i, account in enumerate(pending)
        ]
        await asyncio.gather(*tasks)

    print(f"\n📊 Done! OK: {_ok[0]} | Failed: {_fail[0]} | Total keys: {len(keys)}")
    _print_keys(keys)
    _update_env_file(keys)


# ─── .env Auto-update ────────────────────────────────────────────

ENV_FILE = DATA_DIR.parent / ".env"
ENV_EXAMPLE = DATA_DIR.parent / ".env.example"


def _update_env_file(keys: list):
    """Auto-update HELIUS_API_KEYS in .env file.
    Creates .env from .env.example if not exists.
    Always loads fresh from helius_keys.json to avoid overwriting newer data.
    """
    # Load fresh from file instead of using stale in-memory copy
    fresh_keys = load_json(KEYS_FILE)
    if fresh_keys:
        keys = fresh_keys
    if not keys:
        return

    all_keys = ",".join(k["api_key"] for k in keys)
    env_line = f"HELIUS_API_KEYS={all_keys}"

    # Create .env from .env.example if it doesn't exist
    if not ENV_FILE.exists():
        if ENV_EXAMPLE.exists():
            content = ENV_EXAMPLE.read_text()
        else:
            content = ""
        # Replace placeholder or append
        if "HELIUS_API_KEYS=" in content:
            lines = content.split("\n")
            new_lines = [
                env_line if line.startswith("HELIUS_API_KEYS=") else line
                for line in lines
            ]
            content = "\n".join(new_lines)
        else:
            content = content.rstrip("\n") + f"\n\n{env_line}\n"
        ENV_FILE.write_text(content)
        print(f"\n📝 Đã tạo .env từ .env.example")
        print(f"   ✅ HELIUS_API_KEYS = {len(keys)} keys")
        return

    # .env exists — update HELIUS_API_KEYS line
    content = ENV_FILE.read_text()
    if "HELIUS_API_KEYS=" in content:
        lines = content.split("\n")
        new_lines = [
            env_line if line.startswith("HELIUS_API_KEYS=") else line
            for line in lines
        ]
        content = "\n".join(new_lines)
    else:
        content = content.rstrip("\n") + f"\n{env_line}\n"

    ENV_FILE.write_text(content)
    print(f"\n📝 Đã cập nhật .env")
    print(f"   ✅ HELIUS_API_KEYS = {len(keys)} keys")


# ─── Show Keys / Manual Add ──────────────────────────────────────

def show_keys():
    keys = load_json(KEYS_FILE)
    if not keys:
        print("❌ No keys found. Run 'auto' or 'signup' first.")
        return
    _print_keys(keys)
    _update_env_file(keys)


def _print_keys(keys: list):
    print(f"\n🔑 {len(keys)} Helius API keys:")
    for k in keys:
        print(f"   {k['email']}: {k['api_key'][:16]}...")


def manual_add():
    accounts = load_json(ACCOUNTS_FILE)
    print("📝 Paste accounts (email|password|recovery), empty line to finish:")
    added = 0
    while True:
        line = input("> ").strip()
        if not line:
            break
        parsed = _parse_account(line)
        if parsed and not any(a["email"] == parsed["email"] for a in accounts):
            accounts.append(parsed)
            added += 1
            print(f"   ✅ {parsed['email']}")
        elif parsed:
            print(f"   ⚠️  Duplicate")
        else:
            print(f"   ❌ Invalid format")
    save_json(ACCOUNTS_FILE, accounts)
    print(f"\n📊 Added {added}. Total: {len(accounts)}")


# ─── CLI ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Helius API Key Farmer")
    sub = parser.add_subparsers(dest="command")

    auto_p = sub.add_parser("auto", help="Full auto: buy + signup + keys")
    auto_p.add_argument("--count", type=int, default=10)

    buy_p = sub.add_parser("buy", help="Buy Gmail accounts only")
    buy_p.add_argument("--count", type=int, default=10)

    signup_p = sub.add_parser("signup", help="Signup Helius for pending accounts")
    signup_p.add_argument("--workers", "-w", type=int, default=1, help="Parallel Chrome workers (default: 1)")
    sub.add_parser("keys", help="Show collected API keys")
    sub.add_parser("add", help="Manually add accounts")

    args = parser.parse_args()

    if args.command == "auto":
        asyncio.run(auto_pipeline(args.count))
    elif args.command == "buy":
        asyncio.run(buy_gmail(args.count))
    elif args.command == "signup":
        asyncio.run(signup_helius(workers=args.workers))
    elif args.command == "keys":
        show_keys()
    elif args.command == "add":
        manual_add()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
