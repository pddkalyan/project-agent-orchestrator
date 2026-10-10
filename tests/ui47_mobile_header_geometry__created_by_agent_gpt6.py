"""Real Chromium viewport geometry regression for Issue #47 mobile header.

Standalone by design: run via the existing free Playwright venv on Windows.
Does not modify the repo, does not access credentials and mocks public GitHub
API responses. This checks actual visible text bounds, not only scrollWidth.
"""
import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import sys


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


JS_GEOMETRY = r"""
() => {
    const vp = window.innerWidth;
    const header = document.querySelector('header.movie-studio-header');
    const brand = document.querySelector('.header-brand');
    const summary = document.querySelector('.header-summary');
    const meta = document.querySelector('.header-meta');
    const status = document.querySelector('#bridge-status');
    if (![header, brand, summary, meta, status].every(Boolean))
        return {error: 'Header selectors missing'};
    const roundRect = el => {
        const r = el.getBoundingClientRect();
        return {x: r.x, y: r.y, left: r.left, top: r.top,
                right: r.right, bottom: r.bottom, width: r.width, height: r.height};
    };
    const elements = {header: roundRect(header), brand: roundRect(brand),
                      summary: roundRect(summary), meta: roundRect(meta),
                      status: roundRect(status)};
    const textFragments = [];
    const iter = document.createTreeWalker(header, NodeFilter.SHOW_TEXT);
    for (let node = iter.nextNode(); node; node = iter.nextNode()) {
        if (!node.textContent.trim()) continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        for (const rect of range.getClientRects()) {
            if (!rect.width || !rect.height) continue;
            textFragments.push({text: node.textContent.trim().slice(0, 100),
                left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom});
        }
    }
    const problems = [];
    for (const [name, rect] of Object.entries(elements)) {
        if (rect.left < -1 || rect.right > vp + 1)
            problems.push(name + ': outside viewport (' +
                rect.left.toFixed(1) + '–' + rect.right.toFixed(1) + ')');
    }
    const headerRect = elements.header;
    for (const fragment of textFragments) {
        if (fragment.left < -1 || fragment.right > vp + 1)
            problems.push('text outside viewport: ' + fragment.text);
        if (fragment.top < headerRect.top - 1 ||
            fragment.bottom > headerRect.bottom + 1)
            problems.push('text outside header: ' + fragment.text);
    }
    if (vp <= 420) {
        if (elements.brand.bottom > elements.summary.top + 1)
            problems.push('brand/status layout overlaps vertically');
        if (elements.status.bottom > elements.meta.top + 1)
            problems.push('status/model metadata overlap vertically');
    }
    const cssDirection = window.getComputedStyle(header).flexDirection;
    if (vp <= 639 && cssDirection !== 'column')
        problems.push('expected stacked header (flex-direction: column), got ' + cssDirection);
    if (!meta.innerText.includes('Live model') ||
        !meta.innerText.includes('Native agent execution'))
        problems.push('model/execution metadata unexpectedly missing');
    if (document.documentElement.scrollWidth > vp + 1)
        problems.push('document horizontally overflows');
    return {viewport: vp, flexDirection: cssDirection,
            header: elements, textFragments, problems};
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="Project root with control_center/web")
    ap.add_argument("--out", required=True, help="Destination for screenshots and JSON report")
    args = ap.parse_args()
    web = Path(args.repo) / "control_center" / "web"
    target = web / "index.html"
    if not target.exists():
        print("ERROR: Missing published UI at " + str(target), file=sys.stderr)
        return 2
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: Playwright not installed in this Python environment.", file=sys.stderr)
        return 2

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(QuietHandler, directory=str(web))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    target_url = "http://127.0.0.1:" + str(server.server_port) + "/index.html"
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                for width, height in [(320, 700), (390, 844), (1440, 900)]:
                    page = browser.new_page(viewport={"width": width, "height": height})
                    try:
                        def api(route):
                            url = route.request.url
                            if "/actions/runs" in url:
                                route.fulfill(status=200, json={"workflow_runs": []})
                            else:
                                route.fulfill(status=200, json=[])
                        page.route("https://api.github.com/**", api)
                        page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(1250)
                        evidence = page.evaluate(JS_GEOMETRY)
                        screenshot = outdir / ("header_" + str(width) + ".png")
                        page.screenshot(path=str(screenshot), full_page=True)
                        evidence["screenshot"] = str(screenshot)
                        evidence["passed"] = not evidence.get("error") and not evidence["problems"]
                        results.append(evidence)
                        label = "PASS" if evidence["passed"] else "FAIL"
                        print(label, width, evidence.get("problems") or evidence.get("error"))
                    finally:
                        page.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
    report = {"cases": results, "passed": sum(1 for x in results if x["passed"]),
              "failed": sum(1 for x in results if not x["passed"]), "skipped": 0}
    (outdir / "header_geometry_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print("Results:", outdir / "header_geometry_report.json")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
