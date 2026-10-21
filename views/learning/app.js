"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { groups: [], selected: "", preview: null, file: null, mode: "knowledge", page: 1, kPage: 1, tab: "feed", busy: false, connected: false };
  let bridge, toastTimer, editorTarget, knowledgeEditorTarget;
  const knowledgeMode = () => state.mode === "knowledge";
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
    for (const id of ["enter-expression", "enter-environment", "knowledge-add", "knowledge-export", "knowledge-status", "knowledge-search", "knowledge-search-button", "knowledge-query", "knowledge-query-button", "knowledge-select-all"])
      $(id).disabled = state.busy || !state.connected || noGroup;
    $("preview-file").disabled = state.busy || !state.connected || noGroup || !state.file;
    $("cancel-upload").disabled = state.busy || !state.connected || !(state.file || state.preview || $("content").value);
    $("commit").disabled = state.busy || !state.connected || !state.preview || noGroup;
    $("prev").disabled = state.busy || !state.connected || state.page <= 1 || noGroup;
    $("next").disabled = state.busy || !state.connected || !state.hasNext || noGroup;
    $("knowledge-prev").disabled = state.busy || !state.connected || state.kPage <= 1 || noGroup;
    $("knowledge-next").disabled = state.busy || !state.connected || !state.kNext || noGroup;
    $("knowledge-approve-selected").disabled = state.busy || !state.connected || noGroup || !document.querySelectorAll(".knowledge-check:checked").length;
    document.querySelectorAll("input[data-unavailable]").forEach((node) => { node.disabled = true; });
  }
  function clearPreview() {
    state.preview = null;
    $("preview").replaceChildren(el("strong", "先给它一些值得学习的话"), el("p", "选好文件后点击“预览所选文件”，或粘贴内容后点击预览。"));
    $("preview").className = "empty";
    $("preview-summary").textContent = knowledgeMode() ? "预览后展示前 5 条概念，确认后进入待审核区。" : "解析后会显示前 5 组问答。";
    $("commit").textContent = knowledgeMode() ? "确认学习环境知识" : "确认学习表达方式";
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
    $("preview").replaceChildren(...data.examples.map(data.mode === "knowledge" ? (record) => knowledgeCard(record, false) : dialogue));
    $("preview-summary").textContent = data.mode === "knowledge" ? `提取 ${data.total} 条待审环境知识，展示前 ${Math.min(5, data.total)} 条。${data.details.format === "chat" ? `共 ${data.details.messages} 条消息；提取可复用概念，不保存原始交易。` : "审核后才参与检索。"}` : `识别到 ${data.total} 组问答，展示前 ${Math.min(5, data.total)} 组。`;
    $("preview-target").textContent = `将导入：${groupName(current())}。重复内容会自动跳过，预览 15 分钟内有效。`;
    lockControls();
  }
  function date(timestamp) {
    return timestamp ? new Date(timestamp * 1000).toLocaleString("zh-CN", { hour12: false }) : "尚未收到新消息";
  }
  function renderHistory() {
    if (knowledgeMode()) {
      const counts = current()?.knowledge_counts;
      $("history").replaceChildren(el("p", counts ? `本群知识：生效 ${counts.active} 条，待审 ${counts.pending} 条。请到“工作环境知识库”查看来源和审核。` : "先选择目标群，再导入业务资料。", "subtle"));
      return;
    }
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
    const switchName = { collect: "从这个群学习", style: "使用自然表达", knowledge: "使用环境知识" }[key];
    input.setAttribute("aria-label", `${groupName(group)}：${switchName}`);
    if (!group.available || (key === "knowledge" && !group.knowledge_available)) input.dataset.unavailable = "true";
    const caption = el("span", group[key] ? "已开启" : "已关闭");
    input.addEventListener("change", () => {
      const wanted = input.checked;
      run(async () => {
        caption.textContent = "保存中…";
        try {
          await bridge.apiPost(path("settings", group.id), { [key]: wanted });
          await loadState();
          notify(`${groupName(group)}：${switchName}已${wanted ? "开启" : "关闭"}`);
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
      else if (!group.knowledge_available) identity.append(el("small", "环境知识总开关已关闭"));
      row.append(identity);
      for (const key of ["collect", "style", "knowledge"]) { const cell = el("td"); cell.append(makeSwitch(group, key)); row.append(cell); }
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
    $("metric-knowledge").textContent = groups.reduce((sum, group) => sum + (group.knowledge_counts?.active || 0), 0);
    $("knowledge-pending-count").textContent = `另有 ${groups.reduce((sum, group) => sum + (group.knowledge_counts?.pending || 0), 0)} 条知识待审核`;
    const labels = groups.map((group) => `${groupName(group)} · ${group.platform_id}`);
    $("target").replaceChildren(...(groups.length ? groups.map((group, index) => {
      const label = labels[index];
      const duplicateLabel = labels.filter((item) => item === label).length > 1;
      const detail = duplicateLabel ? `${groupName(group) === group.group_id ? "" : ` · 群 ${group.group_id}`} · 机器人 ${group.bot_id}` : "";
      const option = el("option", label + detail);
      option.value = String(group.id);
      return option;
    }) : [el("option", "暂未发现群，请让目标群发一条消息后刷新")]));
    if (!groups.some((group) => String(group.id) === state.selected)) {
      state.selected = groups.length ? String(groups[0].id) : "";
      clearPreview();
    }
    $("target").value = state.selected;
    $("target-hint").textContent = current() && !current().available ? "这个群受插件总开关或白名单限制，开关设置暂不生效。" : "知识与表达按群保存，不会把其他群的约定套过来。";
    renderHistory(); renderGroups(); lockControls();
  }
  async function loadState(refresh = false) {
    const data = refresh ? await bridge.apiPost("panel/refresh", {}) : await bridge.apiGet("panel/state");
    state.groups = data.groups;
    renderState();
    if (refresh) {
      $("notice").className = "notice";
      $("notice").textContent = [data.identity_notice, ...(data.notes || [])].filter(Boolean).join("\n") || "已连接。选择一个群，上传记录或管理学习开关。";
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
  function resetUpload() {
    state.file = null; $("file").value = ""; $("filename").textContent = "尚未选择文件";
    $("content").value = ""; clearPreview();
  }
  function chooseFile(file) {
    if (!file) return;
    resetUpload();
    if (!current()) throw new Error("请先选择一个目标群。");
    if (!/\.(json|jsonl|txt|md)$/i.test(file.name)) throw new Error("请选择 JSON、JSONL、TXT 或 Markdown 文件。");
    if (file.size > 2 * 1024 * 1024) throw new Error("单个文件最多 2 MiB。");
    state.file = file;
    $("filename").textContent = `${file.name} · ${(file.size / 1024).toFixed(1)} KiB · 尚未上传，点击预览继续`;
    lockControls();
  }
  function setMode(mode) {
    state.mode = mode; resetUpload();
    for (const [id, value] of [["enter-expression", "expression"], ["enter-environment", "knowledge"]]) {
      $(id).classList.toggle("selected", mode === value); $(id).setAttribute("aria-pressed", String(mode === value));
    }
    $("feed-title").textContent = knowledgeMode() ? "熟悉工作环境" : "教它怎么说话";
    $("mode-help").textContent = knowledgeMode() ? "学习术语、部件区别与工作流程，过滤具体交易金额和客户信息。提取结果需审核。" : "投喂整理好的优质问答，只学习措辞与语气，不把样例中的业务内容当成事实。";
    $("content").placeholder = knowledgeMode() ? "2026-01-02 09:00:00 群成员\n发动机盖也叫机盖，倒车镜要区分总成和镜片。" : "问：谢谢，解决了。\n答：解决了就好，后面有问题再喊我。";
    renderHistory();
  }
  document.querySelectorAll(".tab").forEach((node) => node.addEventListener("click", () => {
    state.tab = node.dataset.tab;
    document.querySelectorAll(".tab").forEach((tab) => { tab.classList.toggle("active", tab === node); tab.setAttribute("aria-current", tab === node ? "page" : "false"); });
    for (const name of ["feed", "groups", "library", "knowledge"]) $("tab-" + name).hidden = name !== state.tab;
    if (state.tab === "library") run(loadSamples);
    if (state.tab === "knowledge") run(loadKnowledge);
  }));
  $("target").addEventListener("change", () => {
    state.selected = $("target").value; state.page = 1; state.kPage = 1;
    resetUpload(); renderHistory(); $("try-results").replaceChildren();
    renderState(); if (state.tab === "library") run(loadSamples); if (state.tab === "knowledge") run(loadKnowledge);
    $("knowledge-results").replaceChildren();
  });
  $("file").addEventListener("change", () => run(async () => chooseFile($("file").files[0])));
  $("preview-file").addEventListener("click", () => run(async () => {
    if (!state.file) throw new Error("请先选择文件");
    clearPreview();
    showPreview(await bridge.upload(path(knowledgeMode() ? "preview-knowledge-file" : "preview-file"), state.file));
  }));
  $("cancel-upload").addEventListener("click", resetUpload);
  const drop = $("dropzone");
  drop.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); if (!$("file").disabled) $("file").click(); } });
  for (const name of ["dragover", "dragleave", "drop"]) drop.addEventListener(name, (event) => {
    event.preventDefault(); drop.classList.toggle("dragover", name === "dragover");
    if (name === "drop" && state.connected) run(async () => chooseFile(event.dataTransfer.files[0]));
  });
  $("preview-text").addEventListener("click", () => run(async () => {
    clearPreview(); showPreview(await bridge.apiPost(path(knowledgeMode() ? "preview-knowledge-text" : "preview-text"), { content: $("content").value, format: $("format").value }));
  }));
  $("content").addEventListener("input", clearPreview);
  $("format").addEventListener("change", clearPreview);
  $("enter-expression").addEventListener("click", () => setMode("expression"));
  $("enter-environment").addEventListener("click", () => setMode("knowledge"));
  $("commit").addEventListener("click", () => run(async () => {
    if (!state.preview || state.preview.groupId !== state.selected) throw new Error("目标群已改变，请重新预览。");
    const preview = state.preview, mode = preview.mode;
    const confirmation = await ask("确认开始学习？", `目标群：${groupName(current())}；入口：${mode === "knowledge" ? "熟悉工作环境" : "教它怎么说话"}；文件：${preview.label}；共 ${preview.total} 条。${mode === "knowledge" ? "确认后进入待审核区，审核通过才会使用。" : "确认后这些表达样例将用于回复参考。"}传错了请取消并更换文件。`);
    if (!confirmation.confirmed) return;
    const result = await bridge.apiPost(path("commit"), { token: preview.token, confirmed: true });
    resetUpload(); await loadState(); notify(`导入完成：新增 ${result.added} 条${mode === "knowledge" ? "待审知识，请到工作环境知识库审核" : "表达"}，跳过重复 ${result.duplicates} 条。`);
  }));
  $("refresh").addEventListener("click", () => run(async () => { await loadState(true); if (state.tab === "library") await loadSamples(); if (state.tab === "knowledge") await loadKnowledge(); }));
  $("template").addEventListener("click", () => run(() => bridge.download("panel/template", { mode: knowledgeMode() ? "knowledge" : "expression" }, knowledgeMode() ? "auto-parts-knowledge.json" : "dialogues.json")));
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
  function knowledgeSelection() {
    const ids = [...document.querySelectorAll(".knowledge-check:checked")].map((input) => Number(input.value));
    $("knowledge-selected").textContent = `已选 ${ids.length} 条`;
    lockControls(); return ids;
  }
  function knowledgeCard(record, editable = true) {
    const card = el("article", undefined, "sample"), heading = el("div", undefined, "knowledge-heading");
    if (editable) {
      const select = el("input"); select.type = "checkbox"; select.value = String(record.id); select.className = "knowledge-check"; select.disabled = record.kind === "history"; if (record.kind === "history") select.dataset.unavailable = "true";
      select.setAttribute("aria-label", `选择知识 ${record.id}`); select.addEventListener("change", knowledgeSelection); heading.append(select);
    }
    heading.append(el("h3", record.title));
    card.append(heading, el("p", `${record.category} · ${{history:"旧版交易片段（不参与检索）",reference:"环境参考知识",rule:"本群沟通约定"}[record.kind] || record.kind}`, "knowledge-source"));
    if (record.kind === "history") card.append(el("p", "旧版内容保留供查看，已停止用于回复。请重新投喂以提取环境概念。", "knowledge-history"));
    card.append(el("p", record.content, "knowledge-body"));
    const source = [record.source_label || state.preview?.label || "上传资料", record.source_time || "观察时间未标注", record.source_line ? `第 ${record.source_line} 行` : "", record.author || ""].filter(Boolean).join(" · ");
    card.append(el("p", source, "knowledge-source"));
    if (record.expires) card.append(el("p", `有效期至 ${date(record.expires)}${record.expires < Date.now()/1000 ? "（已过期，不参与检索）" : ""}`, "knowledge-source"));
    if (record.evidence) {
      const evidence = el("details", undefined, "knowledge-evidence"); evidence.append(el("summary", "查看提取依据"), el("p", record.evidence)); card.append(evidence);
    }
    if (editable) {
      const actions = el("div", undefined, "sample-actions");
      if (record.status === "pending" && record.kind !== "history") actions.append(button("审核采纳", () => changeKnowledge("approve", [record.id]), ""));
      if (record.status === "active") actions.append(button("停用", () => changeKnowledge("disable", [record.id])));
      if (record.status === "disabled" && record.kind !== "history") actions.append(button("恢复参考", () => changeKnowledge("restore", [record.id])));
      actions.append(button("修改", async () => openKnowledgeEditor(record)));
      actions.append(button("删除", async () => {
        const result = await ask("删除这条知识？", "删除后不再检索，原始来源片段也会从该条目中移除。");
        if (result.confirmed) await changeKnowledge("delete", [record.id]);
      }, "danger")); card.append(actions);
    }
    return card;
  }
  async function loadKnowledge() {
    if (!current()) { $("knowledge-items").replaceChildren(el("p", "请先选择一个群，再投喂业务记录。", "empty")); return; }
    const result = await bridge.apiGet(path("knowledge"), { status: $("knowledge-status").value, query: $("knowledge-search").value, page: state.kPage });
    state.kNext = state.kPage * result.size < result.total;
    $("knowledge-count").textContent = `共 ${result.total} 条`;
    $("knowledge-page-label").textContent = state.kPage;
    $("knowledge-select-all").checked = false;
    $("knowledge-items").replaceChildren(...(result.items.length ? result.items.map((record) => knowledgeCard(record)) : [el("p", "当前没有这类知识。可以先上传原始群聊，或新增业务规则。", "empty")]));
    knowledgeSelection();
  }
  async function changeKnowledge(action, ids) {
    await bridge.apiPost(path("knowledge-action"), { action, ids });
    await loadState(); await loadKnowledge(); notify("环境知识库已更新。");
  }
  function openKnowledgeEditor(record) {
    knowledgeEditorTarget = { id: record?.id, groupId: state.selected };
    $("knowledge-editor-title").textContent = record ? "修改业务知识" : "新增业务规则 / 参考资料";
    $("knowledge-title").value = record?.title || "";
    $("knowledge-content").value = record?.content || "";
    $("knowledge-category").value = record?.category || "业务参考";
    $("knowledge-kind").value = record?.kind || "reference";
    $("knowledge-kind").disabled = !!record;
    $("knowledge-expires").value = record?.expires ? new Date(record.expires * 1000 - new Date().getTimezoneOffset()*60000).toISOString().slice(0,16) : "";
    $("knowledge-expires").disabled = false;
    $("knowledge-editor").showModal();
  }
  $("knowledge-status").addEventListener("change", () => { state.kPage = 1; run(loadKnowledge); });
  $("knowledge-search-button").addEventListener("click", () => { state.kPage = 1; run(loadKnowledge); });
  $("knowledge-search").addEventListener("keydown", (event) => { if (event.key === "Enter") { state.kPage = 1; run(loadKnowledge); } });
  $("knowledge-prev").addEventListener("click", () => { if (state.kPage > 1) { state.kPage--; run(loadKnowledge); } });
  $("knowledge-next").addEventListener("click", () => { if (state.kNext) { state.kPage++; run(loadKnowledge); } });
  $("knowledge-select-all").addEventListener("change", () => { document.querySelectorAll(".knowledge-check").forEach((input) => { if (!input.disabled) input.checked = $("knowledge-select-all").checked; }); knowledgeSelection(); });
  $("knowledge-approve-selected").addEventListener("click", () => run(async () => {
    const ids = knowledgeSelection(); if (!ids.length) return;
    const result = await ask("审核采纳所选知识？", `将采纳 ${ids.length} 条，确认内容准确且适合当前群。请核对别名关系、部件区别和场景说明。`);
    if (result.confirmed) await changeKnowledge("approve", ids);
  }));
  $("knowledge-add").addEventListener("click", () => run(async () => openKnowledgeEditor(null)));
  $("knowledge-cancel-edit").addEventListener("click", () => $("knowledge-editor").close());
  $("knowledge-edit-form").addEventListener("submit", (event) => {
    event.preventDefault(); const save = $("knowledge-edit-form").querySelector("button[type=submit]");
    run(async () => {
      save.disabled = true;
      try {
        await bridge.apiPost(path("knowledge-action", knowledgeEditorTarget.groupId), {
          action: knowledgeEditorTarget.id ? "edit" : "add", id: knowledgeEditorTarget.id,
          title: $("knowledge-title").value, content: $("knowledge-content").value,
          category: $("knowledge-category").value, kind: $("knowledge-kind").value,
          expires: $("knowledge-expires").value ? new Date($("knowledge-expires").value).getTime()/1000 : null,
        });
        $("knowledge-editor").close(); await loadState(); await loadKnowledge(); notify("已保存到待审核知识。");
      } finally { save.disabled = false; }
    });
  });
  $("knowledge-export").addEventListener("click", () => run(() => bridge.download(path("knowledge-export"), {}, `auto-parts-knowledge-${state.selected}.json`)));
  $("knowledge-query-button").addEventListener("click", () => run(async () => {
    const result = await bridge.apiPost(path("knowledge-retrieve"), { query: $("knowledge-query").value });
    $("knowledge-results").replaceChildren(...(result.items.length ? result.items.map((record) => knowledgeCard(record, false)) : [el("p", "没有找到已审核且未过期的相关知识。", "empty")]));
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
      setMode(state.mode);
      await run(async () => { await loadState(); await loadState(true); });
    } catch (error) {
      $("notice").className = "notice error"; $("notice").textContent = error.message;
    }
    lockControls();
  }
  start();
})();
