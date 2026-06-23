"""Headless Playwright end-to-end test of the DEPLOYED UI (through CloudFront).

Exercises every user-facing feature against the live deployment:
  1. load the SPA
  2. upload a video (presigned PUT to S3, direct from the browser)
  3. see it appear in the video list
  4. wait for transcription to complete (WhisperX on the CPU instance)
  5. select multiple target languages and generate subtitles
  6. wait for the translation tracks to complete (Bedrock Haiku)
  7. open the detail page, confirm tracks + download links

Screenshots are written to deploy/ui-evidence/. Run:  python3 deploy/ui_test.py
"""

import os
import sys
import time

from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get("UI_BASE")  # set to your CloudFront URL, e.g. https://xxxx.cloudfront.net
if not BASE:
    sys.exit("set UI_BASE to the deployment URL, e.g. UI_BASE=https://xxxx.cloudfront.net")
VIDEO = os.environ.get("UI_VIDEO", "/tmp/test_clip.mp4")
LANGS = ["ja", "zh"]  # target languages to select (source will be en)
OUT = os.path.join(os.path.dirname(__file__), "ui-evidence")
os.makedirs(OUT, exist_ok=True)

STEP = 0


def shot(page, name):
    global STEP
    STEP += 1
    path = os.path.join(OUT, f"{STEP:02d}-{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"  screenshot -> {path}")


def main():
    print(f"Testing UI at {BASE}")
    assert os.path.isfile(VIDEO), f"test video missing: {VIDEO}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_default_timeout(30000)

        # 1. Load the SPA
        print("[1] loading SPA")
        page.goto(BASE, wait_until="networkidle")
        expect(page.get_by_role("heading", name="视频字幕", exact=True)).to_be_visible()
        shot(page, "spa-loaded")

        # 2. Upload a video (hidden file input)
        print("[2] uploading video")
        filename = os.path.basename(VIDEO)
        page.set_input_files('[data-testid="file-input"]', VIDEO)
        # Upload button shows progress, then the row appears.
        page.wait_for_selector('[data-testid="media-table"]', timeout=60000)
        shot(page, "uploaded-listed")

        # 3. Identify OUR row by filename (not .first — other media may exist and
        # sort above ours, e.g. an unrelated upload still processing).
        row = page.locator('tr[data-testid^="media-row-"]', has=page.get_by_role("link", name=filename)).first
        expect(row).to_be_visible(timeout=30000)
        media_testid = row.get_attribute("data-testid")
        media_id = int(media_testid.rsplit("-", 1)[1])
        print(f"    media_id={media_id} (file={filename})")

        # 4. Wait for transcription to complete (poll the row's status badge)
        print("[4] waiting for transcription")
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            status = row.locator(".status-badge").first.inner_text().strip()
            if status in ("completed", "failed"):
                break
            page.wait_for_timeout(4000)
        print(f"    transcription status={status}")
        assert status == "completed", f"transcription did not complete: {status}"
        shot(page, "transcribed")

        # 5. Select target languages + generate
        print(f"[5] selecting languages {LANGS} + generate")
        for code in LANGS:
            cb = page.locator(f'[data-testid="lang-{code}"]')
            if not cb.is_checked():
                cb.check()
        # The generate button is gated on the row's transcription status (React
        # Query state); wait until it's actually enabled before clicking.
        gen_btn = page.locator(f'[data-testid="gen-{media_id}"]')
        expect(gen_btn).to_be_enabled(timeout=30000)
        gen_btn.click()

        # Fail fast: the target-language badges must appear shortly after the
        # click (the generate request created pending tracks). If they don't,
        # the click didn't register — don't hang in the completion loop.
        print("[6] confirming tracks were created")
        lang_cell = page.locator(f'[data-testid="media-row-{media_id}"] td').nth(5)
        for code in LANGS:
            expect(lang_cell.locator(".status-badge", has_text=code)).to_be_visible(timeout=30000)
        shot(page, "tracks-created")

        # Wait for all target tracks to settle (completed/failed, not pending/processing)
        print("[6b] waiting for translations to complete")
        deadline = time.monotonic() + 600
        ok = False
        while time.monotonic() < deadline:
            klasses = [
                b.get_attribute("class")
                for b in lang_cell.locator(".status-badge").element_handles()
            ]
            if klasses and not any(
                "pending" in (k or "") or "processing" in (k or "") for k in klasses
            ):
                ok = True
                break
            page.wait_for_timeout(4000)
        print(f"    tracks={lang_cell.locator('.status-badge').all_inner_texts()} settled={ok}")
        assert ok, "translation tracks did not settle in time"
        shot(page, "subtitles-generated")

        # 7. Open detail page, verify tracks + download buttons enabled
        print("[7] opening detail page")
        page.locator(f'[data-testid="media-row-{media_id}"] a').click()
        page.wait_for_selector('[data-testid="tracks-table"]', timeout=30000)
        for code in ["en"] + LANGS:
            expect(page.locator(f'[data-testid="track-{code}"]')).to_be_visible()
        shot(page, "detail-tracks")

        # Verify a download link resolves (SRT for first target language)
        print("[8] verifying subtitle download link")
        dl = page.locator(f'[data-testid="dl-srt-{LANGS[0]}"]')
        expect(dl).to_be_enabled()

        # 9. DEEP-LINK / REFRESH — directly load /media/{id} in a COLD context
        # (empty cache, like a first-time visitor or a hard refresh). This is the
        # path a click-through test misses; a stale cached index.html pointing at
        # a missing JS bundle would render a blank #root here.
        print("[9] deep-link + refresh /media/{id} in a cold browser context")
        deep_url = f"{BASE}/media/{media_id}"
        cold = browser.new_context()  # fresh cache
        dpage = cold.new_page()
        js_404 = []
        dpage.on("response", lambda r: js_404.append(r.url)
                 if r.status >= 400 and (".js" in r.url or ".css" in r.url) else None)
        for label in ("deep-load", "reload"):
            if label == "reload":
                dpage.reload(wait_until="networkidle")
            else:
                dpage.goto(deep_url, wait_until="networkidle", timeout=30000)
            dpage.wait_for_timeout(1500)
            root_html_len = dpage.eval_on_selector("#root", "el => el.innerHTML.length")
            print(f"    {label}: #root innerHTML length = {root_html_len}")
            assert root_html_len and root_html_len > 50, f"{label}: blank #root on deep link"
            # The detail page heading must render (proves the route booted, not just the shell)
            expect(dpage.get_by_role("heading").filter(has_text="/").first).to_be_visible(timeout=15000)
        assert not js_404, f"asset 404s on deep link (stale-cache bug): {js_404}"
        shot(dpage, "deep-link-direct-load")
        cold.close()

        # 10. Verify index.html is served no-cache (the fix that prevents the blank page)
        print("[10] checking index.html cache headers")
        import urllib.request
        req = urllib.request.Request(f"{BASE}/media/{media_id}")
        with urllib.request.urlopen(req, timeout=20) as r:
            cc = (r.headers.get("Cache-Control") or "").lower()
        print(f"    index Cache-Control: {cc!r}")
        assert "no-cache" in cc or "no-store" in cc, f"index.html must not be cacheable, got {cc!r}"

        browser.close()
        print("\nALL UI STEPS PASSED")
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"\nUI TEST FAILED: {exc}")
        sys.exit(1)
