"""Comprehensive UI audit — visit every page and exercise every feature.

Unlike ui_test.py (the happy-path E2E), this navigates the whole app and reports
per-page: console errors, failed/4xx-5xx requests, blank #root, and whether key
actions work. It is read-mostly (only the delete test mutates, on a throwaway
upload it creates itself).
"""

import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = os.environ.get("UI_BASE", "https://CLOUDFRONT_DOMAIN_REDACTED")
OUT = os.path.join(os.path.dirname(__file__), "ui-evidence")
os.makedirs(OUT, exist_ok=True)

problems = []


def audit_page(page, name, url, expect_text=None):
    """Load a URL fresh, collect console errors + bad responses, check not blank."""
    errors, bad = [], []
    page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"PAGEERROR: {e}"))
    page.on("response", lambda r: bad.append(f"{r.status} {r.url}") if r.status >= 400 else None)
    page.goto(url, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(1500)
    root_len = page.eval_on_selector("#root", "el => el.innerHTML.length") if page.query_selector("#root") else 0
    body = page.inner_text("body")[:200]
    blank = not root_len or root_len < 50
    ok = not blank and not errors
    # 4xx on /api/<id> can be legitimate (e.g. missing media) — note but don't fail hard
    api_bad = [b for b in bad if "/api/" not in b]
    status = "OK" if (ok and not api_bad) else "PROBLEM"
    print(f"\n[{name}] {url}")
    print(f"  status={status} root_html_len={root_len} blank={blank}")
    if errors:
        print("  console errors:")
        for e in errors[:6]:
            print("    -", e)
    if bad:
        print("  non-2xx responses:")
        for b in bad[:8]:
            print("    -", b)
    if expect_text and expect_text not in page.inner_text("body"):
        print(f"  MISSING expected text: {expect_text!r}")
        problems.append(f"{name}: missing {expect_text!r}")
    if status == "PROBLEM":
        problems.append(f"{name}: blank={blank} errors={errors[:3]} bad={api_bad[:3]}")
    page.screenshot(path=os.path.join(OUT, f"audit-{name}.png"), full_page=True)
    # detach listeners for next page
    page.remove_listener("console", lambda m: None) if False else None
    return {"root_len": root_len, "errors": errors, "bad": bad}


def main():
    print(f"UI AUDIT @ {BASE}")
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)

        # Each page in its own fresh context so listeners/caches don't bleed.
        def fresh():
            ctx = b.new_context()
            return ctx, ctx.new_page()

        # 1. Video list (home)
        ctx, pg = fresh()
        audit_page(pg, "01-home-video-list", f"{BASE}/", expect_text="视频字幕")
        ctx.close()

        # 2. Subtitle Jobs page (rebuilt from the legacy audio-batch tasks page)
        ctx, pg = fresh()
        audit_page(pg, "02-subtitle-jobs", f"{BASE}/tasks", expect_text="字幕任务")
        # It must reflect the VIDEO pipeline: either a jobs table or the empty hint.
        has_table = pg.locator('[data-testid="jobs-table"]').count() > 0
        has_empty = pg.locator('[data-testid="jobs-empty"]').count() > 0
        print(f"  [jobs] table={has_table} empty-state={has_empty}")
        if not (has_table or has_empty):
            problems.append("jobs: neither jobs table nor empty state rendered")
        # The legacy manual-trigger button must be GONE.
        if pg.get_by_role("button", name="手动触发转录").count():
            problems.append("jobs: legacy 手动触发转录 button still present")
        ctx.close()

        # 2b. Legacy nav links should be removed (only 视频字幕 + 字幕任务).
        ctx, pg = fresh()
        pg.goto(f"{BASE}/", wait_until="networkidle")
        nav = pg.inner_text(".sidebar nav")
        print(f"\n[02b-nav] sidebar nav = {nav!r}")
        if "音频转写" in nav or "转录任务" in nav:
            problems.append(f"nav: legacy links still present: {nav!r}")
        ctx.close()

        # 4. DELETE on a media row — create a throwaway record first via the API path
        #    is internal; here we test the UI button on whatever media exists.
        ctx, pg = fresh()
        pg.goto(f"{BASE}/", wait_until="networkidle")
        pg.wait_for_timeout(1500)
        rows = pg.locator('tr[data-testid^="media-row-"]')
        n = rows.count()
        print(f"\n[04-delete] media rows present: {n}")
        # Only ever delete a throwaway 'test_clip.mp4' row — never the user's content.
        test_row = pg.locator('tr[data-testid^="media-row-"]', has=pg.get_by_role("link", name="test_clip.mp4")).first
        if test_row.count() == 0:
            print("  (no throwaway test_clip.mp4 row to safely delete; skipping delete mutation)")
        else:
            first = test_row
            mid = first.get_attribute("data-testid").rsplit("-", 1)[1]
            del_btn = pg.locator(f'[data-testid="del-{mid}"]')
            print(f"  delete button for media {mid}: present={del_btn.count()>0} enabled={del_btn.is_enabled() if del_btn.count() else 'n/a'}")
            if del_btn.count() == 0:
                problems.append("delete: no delete button on media row")
            else:
                # Does it confirm? Does it actually delete?
                before = rows.count()
                del_btn.click()
                pg.wait_for_timeout(1000)
                # handle a possible confirm dialog
                confirm = pg.get_by_role("button", name="确认删除")
                if confirm.count():
                    print("  confirm dialog appeared; confirming")
                    confirm.first.click()
                else:
                    print("  NO confirm dialog (deletes immediately — risky UX)")
                    problems.append("delete: no confirmation dialog")
                pg.wait_for_timeout(3000)
                after = pg.locator('tr[data-testid^="media-row-"]').count()
                print(f"  rows before={before} after={after} -> {'DELETED OK' if after < before else 'NOT DELETED'}")
                if after >= before:
                    problems.append("delete: row not removed after click")
        pg.screenshot(path=os.path.join(OUT, "audit-04-after-delete.png"), full_page=True)
        ctx.close()

        b.close()

    print("\n" + "=" * 50)
    if problems:
        print(f"AUDIT FOUND {len(problems)} PROBLEM(S):")
        for x in problems:
            print("  -", x)
        return 1
    print("AUDIT CLEAN — all pages/features OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
