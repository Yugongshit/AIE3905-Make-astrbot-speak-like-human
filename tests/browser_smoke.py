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
                    args=["--no-sandbox", "--disable-gpu"],
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
                    frame.locator("#enter-expression").click()
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
                    # Selecting a file must not upload, parse, or write anything.
                    expect(frame.locator("#filename")).to_contain_text("尚未上传")
                    expect(frame.locator("#commit")).to_be_disabled()
                    assert (
                        plugin.learning.memory.stats(plugin.learning.memory.groups()[0]["scope"])[
                            "active"
                        ]
                        == 0
                    )
                    frame.locator("#preview-file").click()
                    expect(frame.locator("#preview-summary")).to_contain_text(
                        "共 1 条", timeout=10000
                    )
                    expect(frame.locator("#commit")).to_be_enabled()
                    frame.locator("#commit").click()
                    expect(frame.locator("#action-description")).to_contain_text("教它怎么说话")
                    frame.locator("#action-dialog button[value=cancel]").click()
                    expect(frame.locator("#metric-active")).to_have_text("0")
                    frame.locator("#commit").click()
                    frame.locator("#action-dialog button[value=confirm]").click()
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
                    frame.locator("#enter-expression").click()
                    frame.locator("#content").fill("问：收到没有？\n答：收到了，谢谢你。")
                    frame.locator("#preview-text").click()
                    expect(frame.locator("#preview")).to_contain_text("收到了，谢谢你。")
                    frame.locator("body").evaluate("()=>window.scrollTo(0,0)")
                    page.evaluate("window.scrollTo(0,0)")
                    expect(frame.locator(".brand")).to_be_in_viewport()
                    page.screenshot(
                        path=str(args.artifacts / "human-style-panel-mobile-0.4.0.png"),
                        full_page=True,
                    )
                    page.set_viewport_size({"width": 1440, "height": 1100})
                    frame.locator("#enter-environment").click()
                    chat = "示例汽配群\n\n2026-01-02 09:00:00 买家\n右前大灯，原厂有吗\n\n2026-01-02 09:01:00 卖家\n右前大灯 原厂777 订货2天\n\n2026-01-02 09:02:00 买家\n[media]\n"
                    frame.locator("#file").set_input_files(
                        {
                            "name": "示例汽配群.txt",
                            "mimeType": "text/plain",
                            "buffer": chat.encode(),
                        }
                    )
                    expect(frame.locator("#commit")).to_be_disabled()
                    frame.locator("#cancel-upload").click()
                    expect(frame.locator("#filename")).to_have_text("尚未选择文件")
                    frame.locator("#file").set_input_files(
                        {
                            "name": "示例汽配群.txt",
                            "mimeType": "text/plain",
                            "buffer": chat.encode(),
                        }
                    )
                    frame.locator("#preview-file").click()
                    expect(frame.locator("#preview-summary")).to_contain_text(
                        "共 4 条", timeout=10000
                    )
                    assert "原厂777" not in frame.locator("#preview").inner_text()
                    frame.locator("#commit").click()
                    expect(frame.locator("#action-description")).to_contain_text("熟悉工作环境")
                    frame.locator("#action-dialog button[value=confirm]").click()
                    expect(frame.locator("#knowledge-pending-count")).to_contain_text(
                        "4 条知识待审核", timeout=10000
                    )
                    expect(frame.locator("#metric-knowledge")).to_have_text("0")
                    frame.locator("[data-tab=knowledge]").click()
                    expect(frame.locator("#knowledge-items")).to_contain_text(
                        "灯具的名称与组成", timeout=10000
                    )
                    frame.locator("#knowledge-select-all").check()
                    frame.locator("#knowledge-approve-selected").click()
                    frame.locator("#action-dialog button[value=confirm]").click()
                    expect(frame.locator("#metric-knowledge")).to_have_text("4", timeout=10000)
                    frame.locator("#knowledge-status").select_option("active")
                    expect(frame.locator("#knowledge-items")).to_contain_text(
                        "环境参考知识", timeout=10000
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
                    shot_page = browser.new_page(viewport={"width": 1440, "height": 1600})
                    shot_page.goto(f"http://127.0.0.1:{port}/test-host")
                    shot_frame = shot_page.frame_locator("iframe")
                    expect(shot_frame.locator("#metric-knowledge")).to_have_text("4", timeout=20000)
                    shot_frame.locator("[data-tab=knowledge]").click()
                    shot_frame.locator("#knowledge-status").select_option("active")
                    expect(shot_frame.locator("#knowledge-items")).to_contain_text(
                        "灯具的名称与组成", timeout=10000
                    )
                    shot_page.evaluate(
                        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
                    )
                    shot_page.screenshot(
                        path=str(args.artifacts / "auto-parts-knowledge-panel-0.4.0.png"),
                        full_page=True,
                    )
                    shot_frame.locator("[data-tab=feed]").click()
                    shot_frame.locator("#enter-environment").click()
                    shot_frame.locator("#content").fill("气门室盖也叫摇臂室盖。倒车镜不是镜片。")
                    shot_frame.locator("#preview-text").click()
                    expect(shot_frame.locator("#commit")).to_be_enabled()
                    shot_frame.locator("body").evaluate("()=>window.scrollTo(0,0)")
                    shot_page.bring_to_front()
                    expect(shot_frame.locator("#commit")).to_be_in_viewport()
                    shot_page.evaluate(
                        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
                    )
                    shot_page.screenshot(
                        path=str(args.artifacts / "human-style-panel-0.4.0.png"), full_page=True
                    )
                    shot_frame.locator("#commit").click()
                    expect(shot_frame.locator("#action-dialog")).to_be_visible()
                    shot_page.evaluate(
                        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
                    )
                    shot_page.screenshot(
                        path=str(args.artifacts / "learning-confirmation-0.4.0.png"), full_page=True
                    )
                    shot_frame.locator("#action-dialog button[value=cancel]").click()
                    shot_page.close()
                    frame.locator("#knowledge-items").get_by_role(
                        "button", name="修改", exact=True
                    ).first.click()
                    expect(frame.locator("#knowledge-kind")).to_be_disabled()
                    frame.locator("#knowledge-content").fill(
                        "大灯总成与灯壳要分别确认，不能混成同一个部件。"
                    )
                    frame.locator("#knowledge-edit-form button[type=submit]").click()
                    expect(frame.locator("#metric-knowledge")).to_have_text("3", timeout=10000)
                    frame.locator("#knowledge-status").select_option("pending")
                    expect(frame.locator("#knowledge-items")).to_contain_text(
                        "大灯总成与灯壳要分别确认", timeout=10000
                    )
                    # Reproduce QQ's mention-dependent self_id directory duplication.
                    scope_key = sys.modules[spec.name + ".human_style.identity"].scope_key
                    Example = sys.modules[spec.name + ".human_style.records"].Example

                    for bot, style in [("qq_official", False), ("mention-user-123", True)]:
                        scope = scope_key("default_1900000000", bot, "group", "QQ-SAME-GROUP")
                        plugin.learning.memory.remember_group(
                            scope,
                            "default_1900000000",
                            bot,
                            "QQ-SAME-GROUP",
                            platform_name="qq_official",
                        )
                        plugin.learning.memory.add(
                            scope, [Example("问题" + bot, "保留的表达")], "manual"
                        )
                        plugin.learning.memory.set_style(scope, style)
                    frame.locator("#refresh").click()
                    expect(frame.locator("#metric-groups")).to_have_text("3", timeout=10000)
                    options = frame.locator("#target option").all_text_contents()
                    assert sum("QQ-SAME-GROUP" in label for label in options) == 1
                    expect(frame.locator("#metric-active")).to_have_text("2")
                    expect(frame.locator("#notice")).to_contain_text("合并 1 条重复群记录")
                    # Distinct OneBot accounts in the same group must not be merged
                    # or displayed as identical dropdown choices.
                    for bot in ("12345", "67890"):
                        scope = scope_key("onebot-case", bot, "group", "same-onebot-group")
                        plugin.learning.memory.remember_group(
                            scope,
                            "onebot-case",
                            bot,
                            "same-onebot-group",
                            "同名协作群",
                            "aiocqhttp",
                        )
                    frame.locator("#refresh").click()
                    expect(frame.locator("#metric-groups")).to_have_text("5", timeout=10000)
                    options = frame.locator("#target option").all_text_contents()
                    assert len(options) == len(set(options))
                    onebot_options = [label for label in options if "同名协作群" in label]
                    assert len(onebot_options) == 2
                    assert any("12345" in label for label in onebot_options)
                    assert any("67890" in label for label in onebot_options)
                    # A real folder picker traverses nested directories and leaves
                    # invalid files out of the final confirmation, without auto-upload.
                    folder = Path(runtime) / "批量演示"
                    (folder / "子目录").mkdir(parents=True)
                    (folder / "a.json").write_text(
                        json.dumps([{"question": "文件夹问题甲", "answer": "文件夹回复甲"}]),
                        encoding="utf-8",
                    )
                    (folder / "子目录/b.txt").write_text(
                        "问：文件夹问题乙\n答：文件夹回复乙", encoding="utf-8"
                    )
                    (folder / "broken.json").write_text("broken JSON", encoding="utf-8")
                    (folder / "ignored.csv").write_text("not a supported record", encoding="utf-8")
                    uploads = []
                    page.on(
                        "request",
                        lambda req: (
                            uploads.append(req.url)
                            if req.method == "POST" and "preview-" in req.url
                            else None
                        ),
                    )
                    frame.locator("[data-tab=feed]").click()
                    frame.locator("#enter-expression").click()
                    before = int(frame.locator("#metric-active").inner_text())
                    frame.locator("#folder").set_input_files(str(folder))
                    expect(frame.locator("#file-queue .queued-file")).to_have_count(3)
                    expect(frame.locator("#file-queue")).to_contain_text("子目录/b.txt")
                    expect(frame.locator("#filename")).to_contain_text("尚未上传")
                    expect(frame.locator("#commit")).to_be_disabled()
                    assert not uploads
                    frame.locator("#preview-file").click()
                    expect(frame.locator("#preview-summary")).to_contain_text(
                        "已预览 2 个所选文件，共 2 条", timeout=10000
                    )
                    expect(frame.locator("#file-queue")).to_contain_text("未导入")
                    expect(frame.locator("#metric-active")).to_have_text(str(before))
                    frame.locator("#commit").click()
                    expect(frame.locator("#action-files li")).to_have_count(2)
                    assert "broken.json" not in frame.locator("#action-files").inner_text()
                    frame.locator("#action-dialog button[value=cancel]").click()
                    expect(frame.locator("#metric-active")).to_have_text(str(before))
                    frame.locator("#commit").click()
                    frame.locator("#action-dialog button[value=confirm]").click()
                    expect(frame.locator("#metric-active")).to_have_text(
                        str(before + 2), timeout=10000
                    )
                    expect(frame.locator("#batch-result")).to_contain_text("已完成 2/2 个文件")
                    expect(frame.locator("#file-queue")).to_contain_text("已导入")
                    bulk_page = browser.new_page(viewport={"width": 1440, "height": 1700})
                    bulk_page.goto(f"http://127.0.0.1:{port}/test-host")
                    bulk_frame = bulk_page.frame_locator("iframe")
                    expect(bulk_frame.locator("#target")).to_be_enabled(timeout=20000)
                    bulk_frame.locator("#enter-expression").click()
                    bulk_frame.locator("#folder").set_input_files(str(folder))
                    bulk_frame.locator("#preview-file").click()
                    expect(bulk_frame.locator("#preview-summary")).to_contain_text(
                        "共 2 条", timeout=10000
                    )
                    bulk_page.evaluate(
                        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
                    )
                    bulk_page.screenshot(
                        path=str(args.artifacts / "batch-folder-panel-0.4.2.png"), full_page=True
                    )
                    bulk_frame.locator("#commit").click()
                    expect(bulk_frame.locator("#action-files li")).to_have_count(2)
                    bulk_page.screenshot(
                        path=str(args.artifacts / "batch-confirmation-0.4.2.png"), full_page=True
                    )
                    bulk_frame.locator("#action-dialog button[value=cancel]").click()
                    bulk_page.close()
                    page.bring_to_front()
                    # Multiple selection plus removing one item must import only
                    # the remaining file, rather than a stale aggregate preview.
                    frame.locator("#cancel-upload").click()
                    frame.locator("#file").set_input_files(
                        [
                            {
                                "name": "keep.json",
                                "mimeType": "application/json",
                                "buffer": json.dumps(
                                    [{"question": "多选问题甲", "answer": "多选回复甲"}]
                                ).encode(),
                            },
                            {
                                "name": "remove.json",
                                "mimeType": "application/json",
                                "buffer": json.dumps(
                                    [{"question": "多选问题乙", "answer": "多选回复乙"}]
                                ).encode(),
                            },
                        ]
                    )
                    expect(frame.locator("#file-queue .queued-file")).to_have_count(2)
                    frame.locator("#preview-file").click()
                    expect(frame.locator("#preview-summary")).to_contain_text("共 2 条")
                    frame.locator(".queued-file").filter(has_text="remove.json").get_by_role(
                        "button", name="移出本次"
                    ).click()
                    expect(frame.locator("#preview-summary")).to_contain_text("共 1 条")
                    frame.locator("#commit").click()
                    expect(frame.locator("#action-files li")).to_have_text(["keep.json"])
                    frame.locator("#action-dialog button[value=confirm]").click()
                    expect(frame.locator("#metric-active")).to_have_text(
                        str(before + 3), timeout=10000
                    )
                    # Exercise drag-and-drop directory APIs including chunked
                    # readEntries results. OS folder drag is not exposed by Playwright.
                    frame.locator("#cancel-upload").click()
                    frame.locator("#dropzone").evaluate("""node => {
                      const file = name => ({isFile:true,isDirectory:false,name,file:cb=>cb(new File(['问：拖入问题\\n答：拖入回复'],name,{type:'text/plain'}))});
                      const directory = (name, chunks) => ({isFile:false,isDirectory:true,name,createReader:()=>{let index=0;return {readEntries:cb=>cb(chunks[index++]||[])}}});
                      const tree = directory('拖入目录', [[file('a.txt')],[directory('嵌套',[[file('b.txt')]])]]);
                      const loose = new File(['问：单独拖入问题\\n答：单独拖入回复'],'loose.txt',{type:'text/plain'});
                      const event = new Event('drop',{bubbles:true,cancelable:true});
                      Object.defineProperty(event,'dataTransfer',{value:{items:[{webkitGetAsEntry:()=>tree},{kind:'file',webkitGetAsEntry:()=>null,getAsFile:()=>loose}],files:[loose]}});
                      node.dispatchEvent(event);
                    }""")
                    expect(frame.locator("#file-queue .queued-file")).to_have_count(3)
                    expect(frame.locator("#file-queue")).to_contain_text("拖入目录/嵌套/b.txt")
                    expect(frame.locator("#commit")).to_be_disabled()
                    assert not errors, errors
                    print(
                        "Browser checks passed: sandboxed bridge, multiple-file and nested-folder selection, chunked folder drop, explicit confirmation, invalid-file exclusion, per-file results, group identity repair, expression and knowledge learning, mobile layout."
                    )
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == "__main__":
    main()
