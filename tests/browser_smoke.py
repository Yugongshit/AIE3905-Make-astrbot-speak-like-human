"""Run the panel in AstrBot's real sandbox/bridge protocol against real HTTP APIs.

python tests/browser_smoke.py --browser-path /usr/bin/chromium --artifacts /tmp/panel-shots
"""

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-path", default=None)
    parser.add_argument("--artifacts", type=Path, default=Path("/tmp/human-style-panel-shots"))
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="human-panel-browser-") as runtime:
        os.environ["ASTRBOT_ROOT"] = runtime
        import uvicorn
        from panel_runtime import make_app
        from playwright.sync_api import expect, sync_playwright

        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "human_style_browser",
            root / "main.py",
            submodule_search_locations=[str(root)],
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        plugin = module.HumanStylePlugin(None, {"collect_candidates": False})
        for name, group in [("采购协作群", "采购群-001"), ("供应商对接群", "供应群-002")]:
            scope = json.dumps(["test-platform", "demo-bot", "group", group], ensure_ascii=False)
            plugin.learning.memory.remember_group(
                scope, "test-platform", "demo-bot", group, name, "test"
            )
        app, _, _ = make_app(plugin, include_host=True)
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=0, access_log=False, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not server.started:
                raise RuntimeError("Panel test server did not start")
            port = server.servers[0].sockets[0].getsockname()[1]
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=args.browser_path,
                    headless=True,
                    args=["--no-sandbox"],
                )
                try:
                    page = browser.new_page(
                        viewport={"width": 1440, "height": 1100}, device_scale_factor=1
                    )
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{port}/test-host")
                    frame = page.frame_locator("iframe")
                    expect(frame.locator("#metric-groups")).to_have_text("2", timeout=20000)
                    expect(frame.locator("#target")).to_be_enabled()
                    frame.locator("#learning-mode").select_option("expression")
                    sample = [
                        {
                            "question": "怎么还没发货？",
                            "answer": "你是担心耽误安排吧，我们先确认一下预计时间。",
                        }
                    ]
                    frame.locator("#file").set_input_files(
                        {
                            "name": "历史记录.json",
                            "mimeType": "application/json",
                            "buffer": json.dumps(sample, ensure_ascii=False).encode(),
                        }
                    )
                    expect(frame.locator("#preview-summary")).to_contain_text(
                        "识别到 1 组", timeout=10000
                    )
                    expect(frame.locator("#commit")).to_be_enabled()
                    page.screenshot(
                        path=str(args.artifacts / "human-style-panel-0.3.0.png"), full_page=True
                    )
                    frame.locator("#commit").click()
                    expect(frame.locator("#metric-active")).to_have_text("1", timeout=10000)
                    frame.locator("[data-tab=groups]").click()
                    first = frame.locator("#group-rows tr").first
                    first.get_by_role("checkbox", name="从这个群学习", exact=False).check()
                    expect(frame.locator("#metric-learning")).to_have_text("1", timeout=10000)
                    first.get_by_role("button", name="备注", exact=True).click()
                    frame.locator("#action-input").fill("重点供应商群")
                    frame.locator("#action-dialog button[value=confirm]").click()
                    expect(frame.locator("#group-rows")).to_contain_text(
                        "重点供应商群", timeout=10000
                    )
                    frame.locator("[data-tab=library]").click()
                    expect(frame.locator("#samples")).to_contain_text("预计时间", timeout=10000)
                    frame.locator("#samples").get_by_role("button", name="修改", exact=True).click()
                    frame.locator("#edit-answer").fill("你着急用吧，我们先把时间确认清楚。")
                    frame.locator("#edit-form button[type=submit]").click()
                    expect(frame.locator("#samples")).to_contain_text(
                        "把时间确认清楚", timeout=10000
                    )
                    frame.locator("#try-query").fill("发货时间")
                    frame.locator("#try-button").click()
                    expect(frame.locator("#try-results")).to_contain_text(
                        "把时间确认清楚", timeout=10000
                    )
                    with page.expect_download() as download:
                        frame.locator("#export").click()
                    assert "把时间确认清楚" in Path(download.value.path()).read_text()
                    frame.locator("#samples").get_by_role("button", name="删除", exact=True).click()
                    frame.locator("#action-dialog button[value=confirm]").click()
                    expect(frame.locator("#sample-count")).to_have_text("共 0 条", timeout=10000)
                    frame.locator("[data-tab=feed]").click()
                    frame.locator("#content").fill(
                        "问：<img src=x onerror=alert(1)>\n答：这一句按文本展示。"
                    )
                    frame.locator("#preview-text").click()
                    expect(frame.locator("#preview")).to_contain_text("<img src=x", timeout=10000)
                    assert frame.locator("#preview img").count() == 0
                    frame.locator("#target").select_option(index=1)
                    expect(frame.locator("#commit")).to_be_disabled()
                    page.reload()
                    expect(frame.locator("#metric-learning")).to_have_text("1", timeout=20000)
                    frame.locator("[data-tab=groups]").click()
                    expect(frame.locator("#group-rows")).to_contain_text("重点供应商群")
                    page.set_viewport_size({"width": 390, "height": 844})
                    frame.locator("[data-tab=feed]").click()
                    expect(frame.locator("#target")).to_be_visible()
                    assert frame.locator("body").evaluate("(e)=>e.scrollWidth <= window.innerWidth")
                    frame.locator("#content").fill("问：收到没有？\n答：收到了，谢谢你。")
                    frame.locator("#preview-text").click()
                    expect(frame.locator("#preview")).to_contain_text("收到了，谢谢你。")
                    frame.locator("body").evaluate("()=>window.scrollTo(0,0)")
                    page.evaluate("window.scrollTo(0,0)")
                    expect(frame.locator(".brand")).to_be_in_viewport()
                    page.screenshot(
                        path=str(args.artifacts / "human-style-panel-mobile-0.3.0.png"),
                        full_page=True,
                    )
                    page.set_viewport_size({"width": 1440, "height": 1100})
                    frame.locator("#learning-mode").select_option("knowledge")
                    chat = "示例汽配群\n\n2026-01-02 09:00:00 买家\n右前大灯，原厂有吗\n\n2026-01-02 09:01:00 卖家\n右前大灯 原厂777 订货2天\n\n2026-01-02 09:02:00 买家\n[media]\n"
                    frame.locator("#file").set_input_files(
                        {
                            "name": "示例汽配群.txt",
                            "mimeType": "text/plain",
                            "buffer": chat.encode(),
                        }
                    )
                    expect(frame.locator("#preview-summary")).to_contain_text(
                        "识别 2 条待审知识", timeout=10000
                    )
                    frame.locator("#commit").click()
                    expect(frame.locator("#knowledge-pending-count")).to_contain_text(
                        "2 条知识待审核", timeout=10000
                    )
                    expect(frame.locator("#metric-knowledge")).to_have_text("0")
                    frame.locator("[data-tab=knowledge]").click()
                    expect(frame.locator("#knowledge-items")).to_contain_text(
                        "原厂777", timeout=10000
                    )
                    frame.locator("#knowledge-select-all").check()
                    frame.locator("#knowledge-approve-selected").click()
                    frame.locator("#action-dialog button[value=confirm]").click()
                    expect(frame.locator("#metric-knowledge")).to_have_text("2", timeout=10000)
                    frame.locator("#knowledge-status").select_option("active")
                    expect(frame.locator("#knowledge-items")).to_contain_text(
                        "历史信息，不代表当前报价", timeout=10000
                    )
                    frame.locator("#knowledge-query").fill("大灯")
                    frame.locator("#knowledge-query-button").click()
                    expect(frame.locator("#knowledge-results")).to_contain_text(
                        "2026-01-02", timeout=10000
                    )
                    frame.locator("body").evaluate("()=>window.scrollTo(0,0)")
                    page.evaluate("window.scrollTo(0,0)")
                    expect(frame.locator(".brand")).to_be_in_viewport()
                    # Capture in a fresh viewport; resizing an opaque iframe can
                    # leave a transient blank compositor surface in headless Chromium.
                    shot_page = browser.new_page(viewport={"width": 1440, "height": 1100})
                    shot_page.goto(f"http://127.0.0.1:{port}/test-host")
                    shot_frame = shot_page.frame_locator("iframe")
                    expect(shot_frame.locator("#metric-knowledge")).to_have_text("2", timeout=20000)
                    shot_frame.locator("[data-tab=knowledge]").click()
                    shot_frame.locator("#knowledge-status").select_option("active")
                    expect(shot_frame.locator("#knowledge-items")).to_contain_text(
                        "原厂777", timeout=10000
                    )
                    shot_page.screenshot(
                        path=str(args.artifacts / "auto-parts-knowledge-panel-0.3.0.png"),
                        full_page=True,
                    )
                    shot_page.close()
                    frame.locator("#knowledge-items").get_by_role(
                        "button", name="修改", exact=True
                    ).first.click()
                    expect(frame.locator("#knowledge-kind")).to_be_disabled()
                    frame.locator("#knowledge-content").fill(
                        "历史报价仅供回溯，当前请向供应商确认。"
                    )
                    frame.locator("#knowledge-edit-form button[type=submit]").click()
                    expect(frame.locator("#metric-knowledge")).to_have_text("1", timeout=10000)
                    frame.locator("#knowledge-status").select_option("pending")
                    expect(frame.locator("#knowledge-items")).to_contain_text(
                        "历史报价仅供回溯", timeout=10000
                    )
                    assert not errors, errors
                    print(
                        "Browser checks passed: sandboxed bridge, expression and raw auto-parts imports, group controls, knowledge approval/query, edit re-review, export, deletion, text-only rendering, reload persistence and mobile layout."
                    )
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == "__main__":
    main()
