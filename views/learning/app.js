"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { groups: [], selected: "", preview: null, page: 1, tab: "feed", busy: false, connected: false };
  let bridge, toastTimer, editorTarget;
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const groupName = (group) => group.alias || group.name || group.group_id;
  const current = () => state.groups.find((group) => String(group.id) === state.selected);
  const path = (action, id = state.selected) => {
    if (!id) throw new Error("请先选择一个目标群。");
    return `panel/groups/${id}/${action}`;
  };
  const notify = (message, error = false) => {
    $("toast").textContent = message;
    $("toast").className = `toast${error ? " error" : ""}`;
    $("toast").hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $("toast").hidden = true; }, error ? 8000 : 4500);
  };
  async function run(task) {
    if (state.busy) return;
    state.busy = true;
    lockControls();
    try { await task(); } catch (error) { notify(error.message || "操作未完成，请重试。", true); }
    finally { state.busy = false; lockControls(); }
  }
  function lockControls() {
    document.querySelectorAll("button, input, select, textarea").forEach((node) => {
      if (node.closest("dialog")) return;
      node.disabled = state.busy || !state.connected;
    });
    const noGroup = !current();
    for (const id of ["preview-text", "export", "search-button", "try-button", "sample-status", "search", "try-query", "content", "format", "file"])
      $(id).disabled = state.busy || !state.connected || noGroup;
    $("commit").disabled = state.busy || !state.connected || !state.preview || noGroup;
    $("prev").disabled = state.busy || !state.connected || state.page <= 1 || noGroup;
    $("next").disabled = state.busy || !state.connected || !state.hasNext || noGroup;
    document.querySelectorAll("input[data-unavailable]").forEach((node) => { node.disabled = true; });
  }
  function clearPreview() {
    state.preview = null;
    $("preview").replaceChildren(el("strong", "先给它一些值得学习的话"), el("p", "上传文件或粘贴对话，即可检查解析结果。"));
    $("preview").className = "empty";
    $("preview-summary").textContent = "解析后会在这里显示前 5 组问答。";
    $("preview-target").textContent = "";
    lockControls();
  }
  function dialogue(record) {
    const block = el("div", undefined, "dialogue");
    block.append(el("small", "对方说"), el("p", record.question), el("small", "希望学会的回复"), el("p", record.answer, "answer"));
    return block;
  }
  function showPreview(data) {
    state.preview = { ...data, groupId: state.selected };
    $("preview").className = "";
    $("preview").replaceChildren(...data.examples.map(dialogue));
    $("preview-summary").textContent = `识别到 ${data.total} 组问答，展示前 ${Math.min(5, data.total)} 组。`;
    $("preview-target").textContent = `将导入：${groupName(current())}。重复内容会自动跳过，预览 15 分钟内有效。`;
    lockControls();
  }
  function date(timestamp) {
    return timestamp ? new Date(timestamp * 1000).toLocaleString("zh-CN", { hour12: false }) : "尚未收到新消息";
  }
  function renderHistory() {
    $("history").replaceChildren(...(current()?.imports || []).map((item) => {
      const row = el("div", undefined, "history-item"), left = el("div");
      left.append(el("strong", item.label), el("span", date(item.created)));
      row.append(left, el("span", `新增 ${item.added} / ${item.total}`));
      return row;
    }));
  }
  function button(text, action, className = "secondary") {
    const node = el("button", text, `button ${className}`);
    node.type = "button";
    node.addEventListener("click", () => run(action));
    return node;
  }
  function ask(title, description, value) {
    const dialog = $("action-dialog"), input = $("action-input");
    $("action-title").textContent = title; $("action-description").textContent = description;
    input.hidden = value === undefined; input.required = value !== undefined; input.value = value ?? "";
    dialog.returnValue = "";
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve({ confirmed: dialog.returnValue === "confirm", value: input.value }), { once: true });
      dialog.showModal();
    });
  }
  function makeSwitch(group, key) {
    const label = el("label", undefined, "switch"), input = el("input");
    input.type = "checkbox";
    input.checked = group[key];
    input.setAttribute("aria-label", `${groupName(group)}：${key === "collect" ? "从这个群学习" : "使用自然表达"}`);
    if (!group.available) input.dataset.unavailable = "true";
    const caption = el("span", group[key] ? "已开启" : "已关闭");
    input.addEventListener("change", () => {
      const wanted = input.checked;
      run(async () => {
        caption.textContent = "保存中…";
        try {
          await bridge.apiPost(path("settings", group.id), { [key]: wanted });
          await loadState();
          notify(`${groupName(group)}：${key === "collect" ? "学习收集" : "自然表达"}已${wanted ? "开启" : "关闭"}`);
        } catch (error) {
          input.checked = group[key]; caption.textContent = group[key] ? "已开启" : "已关闭";
          throw error;
        }
      });
    });
    label.append(input, caption);
    return label;
  }
  function renderGroups() {
    $("group-rows").replaceChildren(...state.groups.map((group) => {
      const row = el("tr"), identity = el("td");
      identity.append(el("strong", groupName(group)), el("small", `群 ${group.group_id}`), el("small", `${group.platform_id} · 机器人 ${group.bot_id}`));
      if (!group.available) identity.append(el("small", "受插件总开关或群白名单限制"));
      row.append(identity);
      for (const key of ["collect", "style"]) { const cell = el("td"); cell.append(makeSwitch(group, key)); row.append(cell); }
      row.append(el("td", `${group.counts.active} / ${group.counts.pending}`));
      const source = el("td");
      source.append(el("span", { message: "收到过消息", platform: "平台同步", memory: "已有学习库", history: "历史会话" }[group.source] || group.source), el("small", date(group.last_seen)));
      row.append(source);
      const actions = el("td");
      actions.append(button("备注", async () => {
        const result = await ask("设置群备注", "给这个群设置一个便于识别的名字（最多 80 字符）", groupName(group));
        if (!result.confirmed) return;
        await bridge.apiPost(path("settings", group.id), { alias: result.value });
        await loadState(); notify("群备注已保存。");
      }));
      row.append(actions);
      return row;
    }));
    $("group-empty").hidden = state.groups.length > 0;
  }
  function renderState() {
    const groups = state.groups;
    $("metric-groups").textContent = groups.length;
    $("metric-learning").textContent = groups.filter((group) => group.collect && group.available).length;
    for (const key of ["active", "pending"]) $("metric-" + key).textContent = groups.reduce((sum, group) => sum + group.counts[key], 0);
    $("target").replaceChildren(...(groups.length ? groups.map((group) => {
      const option = el("option", `${groupName(group)} · ${group.platform_id}`);
      option.value = String(group.id);
      return option;
    }) : [el("option", "暂未发现群，请让目标群发一条消息后刷新")]));
    if (!groups.some((group) => String(group.id) === state.selected)) {
      state.selected = groups.length ? String(groups[0].id) : "";
      clearPreview();
    }
    $("target").value = state.selected;
    $("target-hint").textContent = current() && !current().available ? "这个群受插件总开关或白名单限制，开关设置暂不生效。" : "上传的记录只进入所选群的表达库。";
    renderHistory(); renderGroups(); lockControls();
  }
  async function loadState(refresh = false) {
    const data = refresh ? await bridge.apiPost("panel/refresh", {}) : await bridge.apiGet("panel/state");
    state.groups = data.groups;
    renderState();
    if (refresh) {
      $("notice").className = "notice";
      $("notice").textContent = data.notes?.length ? data.notes.join("\n") : "已连接。选择一个群，上传记录或管理学习开关。";
    }
  }
  async function loadSamples() {
    if (!current()) {
      $("samples").replaceChildren(el("p", "请先选择一个已发现的群。", "empty"));
      $("sample-count").textContent = ""; return;
    }
    const data = await bridge.apiGet(path("samples"), { status: $("sample-status").value, query: $("search").value, page: state.page });
    state.hasNext = state.page * data.size < data.total;
    $("sample-count").textContent = `共 ${data.total} 条`;
    $("page-label").textContent = state.page;
    $("samples").replaceChildren(...(data.items.length ? data.items.map(sampleCard) : [el("p", "当前没有符合条件的记录。", "empty")]));
    lockControls();
  }
  function sampleCard(sample) {
    const card = el("article", undefined, "sample"), head = el("div", undefined, "sample-head");
    const sourceNames = { panel: "面板导入", manual: "手动添加", live_candidate: "群聊候选", feedback: "反馈纠正", import: "文件导入", upload: "群内上传" };
    head.append(el("span", `#${sample.id} · ${sourceNames[sample.source] || sample.source} · 权重 ${sample.quality}`), el("span", date(sample.created)));
    const actions = el("div", undefined, "sample-actions");
    const change = async (action, extra = {}) => {
      await bridge.apiPost(path("sample"), { id: sample.id, action, ...extra });
      await loadState(); await loadSamples(); notify("已更新表达库。");
    };
    if (sample.status === "pending") actions.append(button("通过并学习", () => change("approve"), ""));
    actions.append(button("修改", async () => {
      editorTarget = { id: sample.id, groupId: state.selected };
      $("edit-question").value = sample.question; $("edit-answer").value = sample.answer;
      $("editor").showModal();
    }));
    if (sample.status === "active") actions.append(button("更值得参考 +1", () => change("rate", { delta: 1 })), button("减少参考 −1", () => change("rate", { delta: -1 })));
    actions.append(button("删除", async () => {
      const result = await ask("移除这条表达？", "移除后将不再参考这条记录。");
      if (result.confirmed) await change("delete");
    }, "danger"));
    card.append(head, dialogue(sample), actions);
    return card;
  }
  async function filePreview(file) {
    if (!file) return;
    clearPreview();
    if (!current()) throw new Error("请先选择一个目标群。");
    if (!/\.(json|jsonl|txt)$/i.test(file.name)) throw new Error("请选择 JSON、JSONL 或 TXT 文件。");
    if (file.size > 2 * 1024 * 1024) throw new Error("单个文件最多 2 MiB。");
    $("filename").textContent = `${file.name} · ${(file.size / 1024).toFixed(1)} KiB`;
    showPreview(await bridge.upload(path("preview-file"), file));
  }
  document.querySelectorAll(".tab").forEach((node) => node.addEventListener("click", () => {
    state.tab = node.dataset.tab;
    document.querySelectorAll(".tab").forEach((tab) => { tab.classList.toggle("active", tab === node); tab.setAttribute("aria-current", tab === node ? "page" : "false"); });
    for (const name of ["feed", "groups", "library"]) $("tab-" + name).hidden = name !== state.tab;
    if (state.tab === "library") run(loadSamples);
  }));
  $("target").addEventListener("change", () => {
    state.selected = $("target").value; state.page = 1;
    clearPreview(); renderHistory(); $("try-results").replaceChildren(); $("filename").textContent = "尚未选择文件"; $("file").value = "";
    renderState(); if (state.tab === "library") run(loadSamples);
  });
  $("file").addEventListener("change", () => run(() => filePreview($("file").files[0])));
  const drop = $("dropzone");
  drop.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); if (!$("file").disabled) $("file").click(); } });
  for (const name of ["dragover", "dragleave", "drop"]) drop.addEventListener(name, (event) => {
    event.preventDefault(); drop.classList.toggle("dragover", name === "dragover");
    if (name === "drop" && state.connected) run(() => filePreview(event.dataTransfer.files[0]));
  });
  $("preview-text").addEventListener("click", () => run(async () => {
    clearPreview(); showPreview(await bridge.apiPost(path("preview-text"), { content: $("content").value, format: $("format").value }));
  }));
  $("content").addEventListener("input", clearPreview);
  $("format").addEventListener("change", clearPreview);
  $("commit").addEventListener("click", () => run(async () => {
    if (!state.preview || state.preview.groupId !== state.selected) throw new Error("目标群已改变，请重新预览。");
    const result = await bridge.apiPost(path("commit"), { token: state.preview.token });
    clearPreview(); await loadState(); notify(`导入完成：新增 ${result.added} 条，跳过重复 ${result.duplicates} 条。`);
  }));
  $("refresh").addEventListener("click", () => run(async () => { await loadState(true); if (state.tab === "library") await loadSamples(); }));
  $("template").addEventListener("click", () => run(() => bridge.download("panel/template", {}, "dialogues.json")));
  $("export").addEventListener("click", () => run(() => bridge.download(path("export"), {}, `expressions-${state.selected}.json`)));
  $("sample-status").addEventListener("change", () => { state.page = 1; run(loadSamples); });
  $("search-button").addEventListener("click", () => { state.page = 1; run(loadSamples); });
  $("search").addEventListener("keydown", (event) => { if (event.key === "Enter") { state.page = 1; run(loadSamples); } });
  $("prev").addEventListener("click", () => { if (state.page > 1) { state.page--; run(loadSamples); } });
  $("next").addEventListener("click", () => { if (state.hasNext) { state.page++; run(loadSamples); } });
  $("cancel-edit").addEventListener("click", () => $("editor").close());
  $("edit-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const save = $("edit-form").querySelector("button[type=submit]");
    run(async () => {
      save.disabled = true;
      try {
        await bridge.apiPost(path("sample", editorTarget.groupId), { action: "edit", id: editorTarget.id, question: $("edit-question").value, answer: $("edit-answer").value });
        $("editor").close(); await loadState(); await loadSamples(); notify("修改已保存。");
      } finally { save.disabled = false; }
    });
  });
  $("try-button").addEventListener("click", () => run(async () => {
    const data = await bridge.apiPost(path("retrieve"), { query: $("try-query").value });
    $("try-results").replaceChildren(...(data.items.length ? data.items.map(dialogue) : [el("p", "本群还没有生效表达，请先投喂或审核候选。", "empty")]));
  }));
  async function start() {
    lockControls();
    try {
      bridge = window.AstrBotPluginView;
      if (!bridge) throw new Error("请从 AstrBot 的“插件可视化视图 → 表达学习控制台”打开此页面。");
      let timer;
      try { await Promise.race([bridge.ready(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("面板连接超时，请从 AstrBot 插件可视化视图重新打开。")), 12000); })]); }
      finally { clearTimeout(timer); }
      state.connected = true;
      await run(async () => { await loadState(); await loadState(true); });
    } catch (error) {
      $("notice").className = "notice error"; $("notice").textContent = error.message;
    }
    lockControls();
  }
  start();
})();
