(() => {
  "use strict";

  const state = {
    sessions: [],
    currentSession: null,
    currentRun: null,
    pollTimer: null,
    devicePollTimer: null,
    devices: [],
    busy: false,
  };

  const els = {};
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

  function byId(id) { return document.getElementById(id); }

  async function call(method, args = {}) {
    const body = new URLSearchParams();
    Object.entries(args).forEach(([key, value]) => {
      if (value !== undefined && value !== null) body.append(key, String(value));
    });
    const response = await fetch(`/api/method/ione_agent.api.${method}`, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Frappe-CSRF-Token": csrfToken,
      },
      body,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.exception) {
      let message = payload.message || "请求失败，请稍后重试。";
      if (payload._server_messages) {
        try {
          const messages = JSON.parse(payload._server_messages).map((item) => JSON.parse(item).message);
          message = messages.filter(Boolean).join("；") || message;
        } catch (_) { /* Keep the fallback message. */ }
      }
      throw new Error(message);
    }
    return payload.message;
  }

  async function deviceCall(method, args = {}) {
    const body = new URLSearchParams();
    Object.entries(args).forEach(([key, value]) => {
      if (value !== undefined && value !== null) body.append(key, String(value));
    });
    const response = await fetch(`/api/method/ione_agent.device_api.${method}`, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Frappe-CSRF-Token": csrfToken,
      },
      body,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.exception) {
      let message = payload.message || "设备请求失败，请稍后重试。";
      if (payload._server_messages) {
        try {
          const messages = JSON.parse(payload._server_messages).map((item) => JSON.parse(item).message);
          message = messages.filter(Boolean).join("；") || message;
        } catch (_) { /* Keep the fallback message. */ }
      }
      throw new Error(message);
    }
    return payload.message;
  }

  function showToast(message) {
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.textContent = message;
    els.toastRegion.appendChild(toast);
    window.setTimeout(() => toast.remove(), 4200);
  }

  function formatTime(value) {
    if (!value) return "刚刚";
    const date = new Date(String(value).replace(" ", "T"));
    if (Number.isNaN(date.getTime())) return "刚刚";
    const delta = Math.max(0, Date.now() - date.getTime());
    if (delta < 60000) return "刚刚";
    if (delta < 3600000) return `${Math.floor(delta / 60000)} 分钟前`;
    if (delta < 86400000) return `${Math.floor(delta / 3600000)} 小时前`;
    return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
  }

  function sessionStatusClass(status) {
    return String(status || "").toLowerCase();
  }

  function renderSessions() {
    els.sessionList.replaceChildren();
    if (!state.sessions.length) {
      const empty = document.createElement("div");
      empty.className = "session-empty";
      empty.textContent = "还没有对话。创建一个新对话开始使用。";
      els.sessionList.appendChild(empty);
      return;
    }
    state.sessions.forEach((session) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "session-item";
      button.setAttribute("aria-current", String(session.name === state.currentSession));

      const dot = document.createElement("span");
      dot.className = `session-status ${sessionStatusClass(session.status)}`;
      dot.setAttribute("aria-hidden", "true");
      const copy = document.createElement("span");
      copy.className = "session-copy";
      const title = document.createElement("strong");
      title.textContent = session.title || "新对话";
      const meta = document.createElement("span");
      meta.textContent = `${formatTime(session.last_message_at)} · ${session.message_count || 0} 条消息`;
      copy.append(title, meta);
      button.append(dot, copy);
      button.addEventListener("click", () => selectSession(session.name));
      els.sessionList.appendChild(button);
    });
  }

  function messageElement(message) {
    const article = document.createElement("article");
    article.className = `message ${message.role === "user" ? "user" : "assistant"}${message.message_type === "error" ? " error" : ""}`;
    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = message.role === "user" ? "我" : "IA";
    const body = document.createElement("div");
    body.className = "message-body";
    const text = document.createElement("div");
    text.textContent = message.content || "";
    const time = document.createElement("span");
    time.className = "message-time";
    time.textContent = formatTime(message.sent_at);
    body.append(text, time);
    article.append(avatar, body);
    return article;
  }

  function renderMessages(messages) {
    els.messageList.replaceChildren(...messages.map(messageElement));
    const hasMessages = messages.length > 0;
    els.emptyState.hidden = hasMessages;
    els.messageList.hidden = !hasMessages;
    if (hasMessages) scrollToBottom(false);
  }

  function scrollToBottom(smooth = true) {
    els.messageScroll.scrollTo({ top: els.messageScroll.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }

  function setGateway(gateway) {
    const healthy = gateway?.status === "healthy";
    els.gatewayIndicator.dataset.status = healthy ? "healthy" : "unavailable";
    els.gatewayLabel.textContent = healthy ? `${gateway.runtime || "UFO³"} · ${gateway.model || "Qwen"}` : "Agent 服务不可用";
    els.deviceStatusDot.classList.toggle("online", Number(gateway?.devices_online || 0) > 0);
  }

  function deviceStatusLabel(status) {
    return { Online: "在线", Offline: "离线", Revoked: "已撤销" }[status] || status || "离线";
  }

  function renderDevices() {
    els.deviceList.replaceChildren();
    const onlineCount = state.devices.filter((device) => device.status === "Online").length;
    els.deviceCount.textContent = `${state.devices.length} 台设备 · ${onlineCount} 台在线`;
    els.deviceStatusDot.classList.toggle("online", onlineCount > 0);
    if (!state.devices.length) {
      const empty = document.createElement("div");
      empty.className = "device-list-empty";
      empty.textContent = "还没有注册设备。请生成安装包并在需要使用的电脑上运行。";
      els.deviceList.appendChild(empty);
      return;
    }
    state.devices.forEach((device) => {
      const row = document.createElement("div");
      row.className = "device-row";
      const dot = document.createElement("span");
      dot.className = `device-row-dot ${String(device.status || "").toLowerCase()}`;
      const copy = document.createElement("div");
      copy.className = "device-row-copy";
      const name = document.createElement("strong");
      name.textContent = device.device_name || device.device_id;
      const meta = document.createElement("span");
      meta.textContent = `${deviceStatusLabel(device.status)} · ${device.platform || "Windows"}`;
      copy.append(name, meta);
      const revoke = document.createElement("button");
      revoke.type = "button";
      revoke.className = "revoke-device";
      revoke.textContent = device.status === "Revoked" ? "已撤销" : "撤销";
      revoke.disabled = device.status === "Revoked";
      revoke.addEventListener("click", () => revokeDevice(device));
      row.append(dot, copy, revoke);
      els.deviceList.appendChild(row);
    });
  }

  async function loadDevices() {
    state.devices = await deviceCall("get_devices") || [];
    renderDevices();
  }

  async function createPairing() {
    els.createPairing.disabled = true;
    try {
      const pairing = await deviceCall("create_pairing");
      els.downloadInstaller.href = pairing.download_url;
      els.pairingExpiry.textContent = `安装包将在 ${new Date(String(pairing.expires_at).replace(" ", "T")).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })} 前有效。`;
      els.pairingPanel.hidden = false;
    } catch (error) {
      showToast(error.message);
    } finally {
      els.createPairing.disabled = false;
    }
  }

  async function revokeDevice(device) {
    if (!window.confirm(`确认撤销设备“${device.device_name || device.device_id}”吗？`)) return;
    try {
      await deviceCall("revoke_device", { device_id: device.device_id });
      await loadDevices();
    } catch (error) {
      showToast(error.message);
    }
  }

  function openDeviceModal() {
    els.deviceModal.hidden = false;
    document.body.classList.add("modal-open");
    window.clearInterval(state.devicePollTimer);
    loadDevices().catch((error) => showToast(error.message));
    state.devicePollTimer = window.setInterval(() => loadDevices().catch(() => {}), 4000);
  }

  function closeDeviceModal() {
    els.deviceModal.hidden = true;
    document.body.classList.remove("modal-open");
    window.clearInterval(state.devicePollTimer);
    state.devicePollTimer = null;
  }

  function activeSession() {
    return state.sessions.find((item) => item.name === state.currentSession);
  }

  function updateTitle() {
    const session = activeSession();
    els.conversationTitle.textContent = session?.title || "新对话";
  }

  async function loadBootstrap(session = null) {
    clearPoll();
    const data = await call("get_bootstrap", session ? { session } : {});
    state.sessions = data.sessions || [];
    state.currentSession = data.selected_session || null;
    renderSessions();
    renderMessages(data.messages || []);
    updateTitle();
    setGateway(data.gateway);
    document.getElementById("agent-app").setAttribute("aria-busy", "false");
    const selected = activeSession();
    if (selected?.status === "Running") await resumeCurrentRun();
  }

  async function selectSession(name) {
    if (name === state.currentSession) { closeSidebar(); return; }
    try {
      await loadBootstrap(name);
      closeSidebar();
    } catch (error) {
      showToast(error.message);
    }
  }

  async function createSession() {
    try {
      const session = await call("create_session");
      state.sessions.unshift(session);
      state.currentSession = session.name;
      renderSessions();
      renderMessages([]);
      updateTitle();
      closeSidebar();
      els.messageInput.focus();
    } catch (error) {
      showToast(error.message);
    }
  }

  function setBusy(value) {
    state.busy = value;
    els.sendMessage.disabled = value;
    els.messageInput.disabled = value;
  }

  async function submitMessage() {
    const message = els.messageInput.value.trim();
    if (!message || state.busy) return;
    setBusy(true);
    try {
      const result = await call("send_message", { message, session: state.currentSession });
      state.currentSession = result.session;
      state.currentRun = result.run?.name || null;
      els.messageInput.value = "";
      resizeInput();
      await refreshCurrentConversation();
      if (result.accepted && state.currentRun) {
        showRun(result.run);
        schedulePoll();
      } else {
        setBusy(false);
      }
    } catch (error) {
      setBusy(false);
      showToast(error.message);
    }
  }

  async function refreshCurrentConversation() {
    const messages = state.currentSession ? await call("get_messages", { session: state.currentSession }) : [];
    renderMessages(messages || []);
    const data = await call("get_bootstrap", state.currentSession ? { session: state.currentSession } : {});
    state.sessions = data.sessions || [];
    renderSessions();
    updateTitle();
  }

  function eventLabel(event) {
    const data = event.data || {};
    return data.message || data.event_name || event.output_type || event.event_type || "UFO³ 执行事件";
  }

  function showRun(run) {
    els.runPanel.hidden = false;
    els.runStage.textContent = run.current_stage || "UFO³ 正在处理";
    const pieces = [];
    if (run.model) pieces.push(run.model);
    if (run.elapsed_seconds) pieces.push(`${Number(run.elapsed_seconds).toFixed(1)} 秒`);
    els.runMeta.textContent = pieces.join(" · ") || "正在建立执行计划";
    els.runProgress.style.width = `${Math.max(4, Math.min(100, Number(run.progress || 0)))}%`;
    els.eventList.replaceChildren();
    (run.events || []).slice(-6).forEach((event) => {
      const li = document.createElement("li");
      li.textContent = eventLabel(event);
      els.eventList.appendChild(li);
    });
    const terminal = ["Completed", "Failed", "Stopped"].includes(run.status);
    els.stopRun.hidden = terminal;
    els.runSpinner.hidden = terminal;
  }

  function clearPoll() {
    if (state.pollTimer) window.clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }

  function schedulePoll(delay = 1400) {
    clearPoll();
    state.pollTimer = window.setTimeout(pollRun, delay);
  }

  async function pollRun() {
    if (!state.currentRun) return;
    try {
      const run = await call("get_run", { run: state.currentRun });
      showRun(run);
      if (["Completed", "Failed", "Stopped"].includes(run.status)) {
        clearPoll();
        state.currentRun = null;
        setBusy(false);
        await refreshCurrentConversation();
        window.setTimeout(() => { els.runPanel.hidden = true; }, 1600);
      } else {
        schedulePoll(run.poll_error ? 3500 : 1400);
      }
    } catch (error) {
      showToast(error.message);
      schedulePoll(4000);
    }
  }

  async function resumeCurrentRun() {
    const session = activeSession();
    if (!session || session.status !== "Running") return;
	const lastRun = session.last_run;
    if (!lastRun) return;
    state.currentRun = lastRun;
    setBusy(true);
    showRun({ status: "Running", current_stage: "正在恢复运行状态", progress: 4, events: [] });
    schedulePoll(250);
  }

  async function stopCurrentRun() {
    if (!state.currentRun) return;
    els.stopRun.disabled = true;
    try {
      const run = await call("stop_run", { run: state.currentRun });
      showRun(run);
      schedulePoll(250);
    } catch (error) {
      showToast(error.message);
    } finally {
      els.stopRun.disabled = false;
    }
  }

  function resizeInput() {
    els.messageInput.style.height = "auto";
    els.messageInput.style.height = `${Math.min(180, els.messageInput.scrollHeight)}px`;
  }

  function openSidebar() {
    els.sessionSidebar.classList.add("open");
    els.sidebarBackdrop.hidden = false;
  }

  function closeSidebar() {
    els.sessionSidebar.classList.remove("open");
    els.sidebarBackdrop.hidden = true;
  }

  function bindElements() {
    ["sessionSidebar", "sidebarBackdrop", "sessionList", "newSession", "refreshSessions", "openSidebar", "closeSidebar",
      "conversationTitle", "gatewayIndicator", "gatewayLabel", "messageScroll", "emptyState", "messageList", "runPanel",
      "runStage", "runMeta", "runProgress", "runSpinner", "eventList", "stopRun", "composer", "messageInput",
      "sendMessage", "toastRegion", "deviceButton", "deviceStatusDot", "deviceModal", "deviceModalBackdrop",
      "closeDeviceModal", "createPairing", "pairingPanel", "downloadInstaller", "pairingExpiry", "refreshDevices",
      "deviceList", "deviceCount"].forEach((name) => {
      const id = name.replace(/[A-Z]/g, (match) => `-${match.toLowerCase()}`);
      els[name] = byId(id);
    });
  }

  function bindEvents() {
    els.newSession.addEventListener("click", createSession);
    els.refreshSessions.addEventListener("click", () => loadBootstrap(state.currentSession).catch((error) => showToast(error.message)));
    els.openSidebar.addEventListener("click", openSidebar);
    els.closeSidebar.addEventListener("click", closeSidebar);
    els.sidebarBackdrop.addEventListener("click", closeSidebar);
    els.stopRun.addEventListener("click", stopCurrentRun);
    els.deviceButton.addEventListener("click", openDeviceModal);
    els.closeDeviceModal.addEventListener("click", closeDeviceModal);
    els.deviceModalBackdrop.addEventListener("click", closeDeviceModal);
    els.createPairing.addEventListener("click", createPairing);
    els.refreshDevices.addEventListener("click", () => loadDevices().catch((error) => showToast(error.message)));
    els.messageInput.addEventListener("input", resizeInput);
    els.messageInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        submitMessage();
      }
    });
    els.composer.addEventListener("submit", (event) => { event.preventDefault(); submitMessage(); });
    document.querySelectorAll(".suggestion").forEach((button) => {
      button.addEventListener("click", () => {
        els.messageInput.value = button.dataset.prompt || "";
        resizeInput();
        els.messageInput.focus();
      });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !els.deviceModal.hidden) closeDeviceModal();
    });
  }

  async function init() {
    bindElements();
    bindEvents();
    try {
      await loadBootstrap();
    } catch (error) {
      document.getElementById("agent-app").setAttribute("aria-busy", "false");
      showToast(error.message);
      setGateway({ status: "unavailable" });
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
