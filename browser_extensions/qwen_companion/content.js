(() => {
  "use strict";

  const HOST_ID = "ione-qwen-companion-v214";
  const SITE_ORIGIN = "https://child.myyr.top";
  const TARGET_MODEL = "Qwen3.8-Max";
  const HIDDEN_SIDEBAR_LABELS = new Set(["社区", "Coder"]);
  const LEGACY_MARKERS = [
    "data-ione-hidden",
    "data-ione-safe-hidden",
    "data-ione-v2-model-button",
    "data-ione-model-hidden",
    "data-ione-v202-hidden",
  ];

  function normalize(value) {
    return (value || "")
      .replace(/\s+/g, " ")
      .replace(/[\u200b-\u200d\ufeff]/g, "")
      .trim();
  }

  function cleanupMarkedElement(element) {
    if (!(element instanceof HTMLElement || element instanceof SVGElement)) return;

    element.style.removeProperty("display");
    element.style.removeProperty("visibility");
    element.style.removeProperty("opacity");
    element.style.removeProperty("pointer-events");

    for (const marker of LEGACY_MARKERS) {
      element.removeAttribute(marker);
    }
  }

  function cleanupLegacyEffects() {
    document.getElementById("ione-qwen-brand-mask")?.remove();
    document.getElementById("ione-qwen-companion")?.remove();
    document.getElementById("ione-qwen-companion-v2")?.remove();
    document.getElementById("ione-qwen-companion-recovery-v210")?.remove();
    document.getElementById("ione-qwen-companion-v211")?.remove();
    document.getElementById("ione-qwen-companion-v212")?.remove();
    document.getElementById("ione-qwen-companion-v213")?.remove();

    const selector = LEGACY_MARKERS.map((marker) => `[${marker}]`).join(",");
    document.querySelectorAll(selector).forEach(cleanupMarkedElement);
  }

  function hideLogoElement(logo) {
    if (!(logo instanceof HTMLImageElement)) return false;

    const rawSrc = logo.getAttribute("src") || "";
    const resolvedSrc = logo.src || "";
    if (!rawSrc.includes("qwen-logo.svg") && !resolvedSrc.includes("qwen-logo.svg")) {
      return false;
    }

    logo.style.setProperty("visibility", "hidden", "important");
    logo.style.setProperty("opacity", "0", "important");
    logo.style.setProperty("pointer-events", "none", "important");
    return true;
  }

  function hideModelTrigger(trigger) {
    if (!(trigger instanceof HTMLElement)) return false;
    if (!trigger.matches("div.wms-trigger__content")) return false;

    const textNode = trigger.querySelector(":scope > div.wms-trigger__text");
    const iconNode = trigger.querySelector(":scope > span.wms-trigger__icon");

    if (!(textNode instanceof HTMLElement)) return false;
    if (!(iconNode instanceof HTMLElement)) return false;
    if (normalize(textNode.textContent) !== TARGET_MODEL) return false;

    trigger.setAttribute("data-ione-v213-model-hidden", "1");
    return true;
  }

  function hideSidebarEntry(entry) {
    if (!(entry instanceof HTMLElement)) return false;
    if (!entry.matches("div.sidebar-entry-list-content")) return false;

    const textNode = entry.querySelector(":scope > div.sidebar-entry-list-text");
    if (!(textNode instanceof HTMLElement)) return false;

    const label = normalize(textNode.textContent);
    if (!HIDDEN_SIDEBAR_LABELS.has(label)) return false;

    entry.setAttribute("data-ione-v214-sidebar-hidden", label);
    return true;
  }

  function applyExactHiding(root = document) {
    const logos = [];

    if (root instanceof Element && root.matches('img.logo-img[alt="logo"]')) {
      logos.push(root);
    }
    if (root.querySelectorAll) {
      logos.push(...root.querySelectorAll('img.logo-img[alt="logo"]'));
    }
    logos.forEach(hideLogoElement);

    const triggers = [];
    if (root instanceof Element && root.matches("div.wms-trigger__content")) {
      triggers.push(root);
    }
    if (root.querySelectorAll) {
      triggers.push(...root.querySelectorAll("div.wms-trigger__content"));
    }
    triggers.forEach(hideModelTrigger);

    const sidebarEntries = [];
    if (root instanceof Element && root.matches("div.sidebar-entry-list-content")) {
      sidebarEntries.push(root);
    }
    if (root.querySelectorAll) {
      sidebarEntries.push(...root.querySelectorAll("div.sidebar-entry-list-content"));
    }
    sidebarEntries.forEach(hideSidebarEntry);
  }

  function link(label, href, sameTab = false) {
    const node = document.createElement("a");
    node.textContent = label;
    node.href = href;
    node.target = sameTab ? "_self" : "_blank";
    node.rel = "noopener noreferrer";
    node.className = "ione-link";
    return node;
  }

  function mountToolbar() {
    if (document.getElementById(HOST_ID)) return;

    const host = document.createElement("div");
    host.id = HOST_ID;
    const shadow = host.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    style.textContent = `
      :host {
        all: initial;
        position: fixed;
        top: 10px;
        left: 50%;
        transform: translateX(-50%);
        z-index: 2147483647;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
          "Microsoft YaHei", sans-serif;
      }
      .bar {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 10px;
        border: 1px solid rgba(15, 118, 110, .18);
        border-radius: 14px;
        background: rgba(255, 255, 255, .96);
        box-shadow: 0 8px 28px rgba(15, 23, 42, .16);
        color: #111827;
        white-space: nowrap;
      }
      .badge {
        padding: 3px 7px;
        border-radius: 7px;
        background: #ecfdf5;
        color: #047857;
        font-size: 11px;
        font-weight: 800;
      }
      .brand {
        font-size: 13px;
        font-weight: 800;
      }
      .ione-link, button {
        appearance: none;
        border: 0;
        border-radius: 9px;
        padding: 6px 9px;
        background: #f3f4f6;
        color: #374151;
        font: inherit;
        font-size: 12px;
        font-weight: 600;
        text-decoration: none;
        cursor: pointer;
      }
    `;

    const bar = document.createElement("nav");
    bar.className = "bar";

    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = "v2.1.4";

    const brand = document.createElement("span");
    brand.className = "brand";
    brand.textContent = "童健云 · 智能助手";

    const home = link("返回童健云", `${SITE_ORIGIN}/desk`, true);

    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.textContent = "刷新页面";
    refresh.addEventListener("click", () => window.location.reload());

    bar.append(badge, brand, home, refresh);
    shadow.append(style, bar);
    document.documentElement.appendChild(host);
  }

  cleanupLegacyEffects();
  mountToolbar();
  applyExactHiding(document);

  // Keep undoing only legacy extension marker side effects.
  const legacyObserver = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (!(mutation.target instanceof Element)) continue;
      if (!LEGACY_MARKERS.includes(mutation.attributeName || "")) continue;
      cleanupMarkedElement(mutation.target);
      applyExactHiding(mutation.target);
    }
  });

  legacyObserver.observe(document.documentElement, {
    subtree: true,
    attributes: true,
    attributeFilter: LEGACY_MARKERS,
  });

  function inspectMutationNode(node) {
    if (node instanceof Element) {
      applyExactHiding(node);

      const containingTrigger = node.closest("div.wms-trigger__content");
      if (containingTrigger instanceof HTMLElement) {
        hideModelTrigger(containingTrigger);
      }

      const containingSidebarEntry = node.closest("div.sidebar-entry-list-content");
      if (containingSidebarEntry instanceof HTMLElement) {
        hideSidebarEntry(containingSidebarEntry);
      }
      return;
    }

    if (node instanceof Text && node.parentElement instanceof HTMLElement) {
      const containingTrigger = node.parentElement.closest("div.wms-trigger__content");
      if (containingTrigger instanceof HTMLElement) {
        hideModelTrigger(containingTrigger);
      }

      const containingSidebarEntry = node.parentElement.closest("div.sidebar-entry-list-content");
      if (containingSidebarEntry instanceof HTMLElement) {
        hideSidebarEntry(containingSidebarEntry);
      }
    }
  }

  // Qwen may mount the model wrapper first and write Qwen3.8-Max as a text
  // node afterwards. Handle both added nodes and later text mutations.
  const mountObserver = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === "characterData") {
        inspectMutationNode(mutation.target);
        continue;
      }

      for (const node of mutation.addedNodes) {
        inspectMutationNode(node);
      }
    }
  });

  mountObserver.observe(document.documentElement, {
    subtree: true,
    childList: true,
    characterData: true,
  });
})();
