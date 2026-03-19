#!/usr/bin/env python3
"""
Download a webpage and all loaded resources for offline viewing.

This script uses Playwright to render the page and capture every successful
network response (HTML/CSS/JS/images/fonts/media/XHR, etc.) while the page loads.
It then saves:
1) all captured resources into a local folder structure
2) an offline `index.html` with URLs rewritten to local files
3) a JSON manifest of URL-to-local-file mapping
"""

from __future__ import annotations

import argparse
import html
import hashlib
import json
import mimetypes
import re
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Set
from urllib.parse import unquote, urljoin, urlsplit

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Missing dependency: playwright")
    print("Install with:")
    print("  pip install playwright")
    print("  python -m playwright install chromium")
    sys.exit(1)


CONTENT_TYPE_EXT = {
    "text/css": ".css",
    "text/javascript": ".js",
    "application/javascript": ".js",
    "application/x-javascript": ".js",
    "application/json": ".json",
    "text/html": ".html",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/x-icon": ".ico",
    "font/woff": ".woff",
    "font/woff2": ".woff2",
    "font/ttf": ".ttf",
    "font/otf": ".otf",
    "application/font-woff": ".woff",
    "application/octet-stream": "",
}


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}" + (f"?{parts.query}" if parts.query else "")


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", value)


def safe_segment(value: str) -> str:
    """Keep Unicode, replace only filesystem-invalid characters."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    cleaned = cleaned.rstrip(" .")
    if not cleaned:
        cleaned = "_"

    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
    if cleaned.upper() in reserved:
        cleaned = f"{cleaned}_"
    return cleaned


def sanitize_output_folder_name(value: str) -> str:
    """Sanitize user-provided folder title for Windows path usage."""
    cleaned = safe_segment(value.strip())
    # Keep names readable while avoiding awkward repeated spaces.
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "offline_site"


def infer_extension(path: str, content_type: str) -> str:
    clean_ct = content_type.split(";")[0].strip().lower() if content_type else ""
    mapped = CONTENT_TYPE_EXT.get(clean_ct)
    if mapped is not None:
        return mapped

    guessed = mimetypes.guess_extension(clean_ct) if clean_ct else None
    if guessed:
        return guessed

    suffix = Path(path).suffix
    return suffix if suffix else ".bin"


class ResourceStore:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.site_dir = output_dir / "site"
        self.url_to_local: Dict[str, str] = {}
        self.lock = threading.Lock()

    def _build_local_path(self, url: str, content_type: str) -> Path:
        parts = urlsplit(url)
        host = safe_name(parts.netloc)
        raw_path = unquote(parts.path or "/")
        path_obj = Path(raw_path)

        if raw_path.endswith("/") or raw_path == "":
            path_obj = path_obj / "index"

        segments = [safe_segment(p) for p in path_obj.parts if p not in ("\\", "/")]
        base_path = Path(*segments) if segments else Path("index")
        if not base_path.suffix:
            ext = infer_extension(str(base_path), content_type)
            base_path = base_path.with_suffix(ext if ext else ".bin")

        if parts.query:
            qhash = hashlib.sha1(parts.query.encode("utf-8")).hexdigest()[:10]
            base_path = base_path.with_name(f"{base_path.stem}_q_{qhash}{base_path.suffix}")

        final_path = self.site_dir / host / base_path
        return final_path

    def save_response(self, url: str, content_type: str, body: bytes) -> Optional[str]:
        normalized = normalize_url(url)
        if not normalized.startswith("http://") and not normalized.startswith("https://"):
            return None

        with self.lock:
            if normalized in self.url_to_local:
                return self.url_to_local[normalized]

            local_path = self._build_local_path(normalized, content_type)
            local_path.parent.mkdir(parents=True, exist_ok=True)

            if local_path.exists():
                # Avoid accidental collisions from different URLs.
                uh = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
                local_path = local_path.with_name(f"{local_path.stem}_{uh}{local_path.suffix}")

            local_path.write_bytes(body)
            rel = local_path.relative_to(self.output_dir).as_posix()
            self.url_to_local[normalized] = rel
            return rel


def rewrite_offline_html(html: str, base_url: str, url_map: Dict[str, str]) -> str:
    attr_pattern = re.compile(
        r"""(?P<prefix>\b(?:src|href|poster|data|action)\s*=\s*["'])(?P<url>[^"'#]+)(?P<suffix>["'])""",
        re.IGNORECASE,
    )
    srcset_pattern = re.compile(
        r"""(?P<prefix>\bsrcset\s*=\s*["'])(?P<value>[^"']+)(?P<suffix>["'])""",
        re.IGNORECASE,
    )
    css_url_pattern = re.compile(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""", re.IGNORECASE)

    def replace_single_url(raw: str) -> str:
        if raw.startswith(("data:", "mailto:", "javascript:", "tel:")):
            return raw
        full = normalize_url(urljoin(base_url, raw))
        return url_map.get(full, raw)

    def attr_repl(match: re.Match) -> str:
        new_url = replace_single_url(match.group("url"))
        return f"{match.group('prefix')}{new_url}{match.group('suffix')}"

    def srcset_repl(match: re.Match) -> str:
        items = [item.strip() for item in match.group("value").split(",")]
        converted = []
        for item in items:
            parts = item.split()
            if not parts:
                continue
            u = replace_single_url(parts[0])
            converted.append(" ".join([u] + parts[1:]))
        return f"{match.group('prefix')}{', '.join(converted)}{match.group('suffix')}"

    def css_repl(match: re.Match) -> str:
        quote = match.group(1) or ""
        raw = match.group(2)
        new_url = replace_single_url(raw)
        return f"url({quote}{new_url}{quote})"

    out = attr_pattern.sub(attr_repl, html)
    out = srcset_pattern.sub(srcset_repl, out)
    out = css_url_pattern.sub(css_repl, out)
    return out


def extract_document_js_urls(document_js_text: str) -> Set[str]:
    """
    Extract Axure page URLs from data/document.js.
    Looks for common patterns like:
      "url":"some_page.html"
      var y="url", z="some_page.html"
    """
    found: Set[str] = set()

    # Generic JSON-like URL fields.
    for m in re.finditer(r'"url"\s*:\s*"([^"]+\.html)"', document_js_text, re.IGNORECASE):
        found.add(m.group(1))

    # Minified Axure variable assignments that end with .html
    for m in re.finditer(r'="([^"]+\.html)"', document_js_text, re.IGNORECASE):
        candidate = m.group(1)
        if "/" in candidate or "__" in candidate or candidate.endswith(".html"):
            found.add(candidate)

    return {u for u in found if u and not u.lower().startswith(("http://", "https://"))}


def crawl_axure_pages(page, base_url: str, store: ResourceStore, timeout_ms: int) -> int:
    document_js_url = normalize_url(urljoin(base_url, "data/document.js"))
    local_doc_rel = store.url_to_local.get(document_js_url)
    if not local_doc_rel:
        return 0

    local_doc_path = store.output_dir / local_doc_rel
    if not local_doc_path.exists():
        return 0

    try:
        text = local_doc_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return 0

    candidates = sorted(extract_document_js_urls(text))
    visited = 0
    for rel in candidates:
        target = urljoin(base_url, rel)
        try:
            page.goto(target, wait_until="networkidle", timeout=timeout_ms)
            time.sleep(0.25)
            visited += 1
        except Exception:
            # Continue with remaining pages even if one fails.
            continue
    return visited


def launch_browser_with_fallback(pw, browser_channel: str):
    if browser_channel != "auto":
        return pw.chromium.launch(headless=True, channel=browser_channel)

    try:
        return pw.chromium.launch(headless=True)
    except PlaywrightError as exc:
        # In restricted corporate environments, Playwright browser binaries may
        # be blocked. Fall back to system-installed browsers.
        message = str(exc)
        if "Executable doesn't exist" not in message:
            raise

    fallback_channels = ("msedge", "chrome")
    for channel in fallback_channels:
        try:
            print(f"Built-in browser not found, trying system browser channel: {channel}")
            return pw.chromium.launch(headless=True, channel=channel)
        except PlaywrightError:
            continue

    raise RuntimeError(
        "Playwright browser executable is missing and no system browser channel "
        "(msedge/chrome) was launchable. Try:\n"
        "1) python -m playwright install chromium\n"
        "2) or run this script with --browser-channel msedge"
    )


def run_download(
    url: str,
    output_dir: Path,
    wait_seconds: float,
    scroll_rounds: int,
    include_cross_origin: bool,
    timeout_ms: int,
    browser_channel: str,
    crawl_all_axure_pages: bool,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    store = ResourceStore(output_dir)
    base_host = urlsplit(url).netloc

    with sync_playwright() as p:
        browser = launch_browser_with_fallback(p, browser_channel)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        def on_response(response):
            try:
                rurl = response.url
                if not include_cross_origin and urlsplit(rurl).netloc != base_host:
                    return
                if response.status < 200 or response.status >= 400:
                    return
                body = response.body()
                if body is None:
                    return
                content_type = response.headers.get("content-type", "")
                store.save_response(rurl, content_type, body)
            except Exception:
                # Keep downloader resilient to occasional response failures.
                return

        page.on("response", on_response)
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        entry_url = page.url

        for _ in range(max(0, scroll_rounds)):
            page.mouse.wheel(0, 2500)
            time.sleep(0.6)

        if wait_seconds > 0:
            time.sleep(wait_seconds)

        if crawl_all_axure_pages:
            visited = crawl_axure_pages(page, entry_url, store, timeout_ms)
            if visited > 0:
                print(f"Crawled additional Axure pages: {visited}")
                page.goto(entry_url, wait_until="networkidle", timeout=timeout_ms)

        rendered_html = page.content()
        final_url = entry_url
        offline_html = rewrite_offline_html(rendered_html, final_url, store.url_to_local)

        # Save rewritten snapshot for debugging/manual fallback.
        (output_dir / "index_rewritten.html").write_text(offline_html, encoding="utf-8")

        # Prefer launching the original saved site index to preserve runtime
        # relative URL behavior (important for Axure-style single-page players).
        main_doc_rel = store.url_to_local.get(normalize_url(final_url))
        hash_fragment = urlsplit(final_url).fragment
        if main_doc_rel:
            target = main_doc_rel + (f"#{hash_fragment}" if hash_fragment else "")
            launcher_html = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<title>Offline Launcher</title>"
                f"<meta http-equiv='refresh' content='0; url={html.escape(target, quote=True)}'>"
                "</head><body>"
                f"<p>Opening offline page: <a href='{html.escape(target, quote=True)}'>{html.escape(target)}</a></p>"
                "</body></html>"
            )
            (output_dir / "index.html").write_text(launcher_html, encoding="utf-8")
        else:
            # Fallback if main document mapping is unexpectedly missing.
            (output_dir / "index.html").write_text(offline_html, encoding="utf-8")

        server_bat = (
            "@echo off\r\n"
            "setlocal\r\n"
            "cd /d %~dp0\r\n"
            "echo Starting local server at http://127.0.0.1:8000\r\n"
            "python -m http.server 8000\r\n"
        )
        (output_dir / "run_offline_server.bat").write_text(server_bat, encoding="utf-8")

        manifest = {
            "source_url": url,
            "final_url": final_url,
            "downloaded_count": len(store.url_to_local),
            "downloaded_at_epoch": int(time.time()),
            "url_to_local": store.url_to_local,
        }
        (output_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        browser.close()

    print(f"Done. Downloaded resources: {len(store.url_to_local)}")
    print(f"Offline entry file: {output_dir / 'index.html'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download webpage resources for offline browser viewing."
    )
    parser.add_argument("url", help="Target webpage URL.")
    parser.add_argument(
        "-o",
        "--output",
        default="offline_site",
        help="Output folder (default: offline_site).",
    )
    parser.add_argument(
        "--output-title",
        default="",
        help="Use this text as output folder name (supports Chinese).",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=3.0,
        help="Extra wait time after load for late requests (default: 3).",
    )
    parser.add_argument(
        "--scroll-rounds",
        type=int,
        default=4,
        help="How many auto-scroll rounds to trigger lazy-load (default: 4).",
    )
    parser.add_argument(
        "--include-cross-origin",
        action="store_true",
        help="Also download resources from other domains.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=120000,
        help="Navigation timeout in milliseconds (default: 120000).",
    )
    parser.add_argument(
        "--browser-channel",
        choices=["auto", "msedge", "chrome"],
        default="auto",
        help="Browser channel: auto/msedge/chrome (default: auto).",
    )
    parser.add_argument(
        "--no-crawl-axure-pages",
        action="store_true",
        help="Disable automatic crawl of Axure pages from data/document.js.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output)
    if args.output_title:
        output_dir = Path(sanitize_output_folder_name(args.output_title))

    run_download(
        url=args.url,
        output_dir=output_dir,
        wait_seconds=args.wait_seconds,
        scroll_rounds=args.scroll_rounds,
        include_cross_origin=args.include_cross_origin,
        timeout_ms=args.timeout_ms,
        browser_channel=args.browser_channel,
        crawl_all_axure_pages=not args.no_crawl_axure_pages,
    )


if __name__ == "__main__":
    main()
