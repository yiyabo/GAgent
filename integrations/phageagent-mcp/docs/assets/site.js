"use strict";

// Everything stays in the browser: this page never accepts tokens or calls MCP.
const filters = [...document.querySelectorAll("[data-filter]")];
const tools = [...document.querySelectorAll(".tool[data-category]")];
const search = document.getElementById("tool-search");
let category = "all";

function filterTools() {
  const query = search.value.trim().toLocaleLowerCase();
  let count = 0;
  for (const tool of tools) {
    const visible =
      (category === "all" || tool.dataset.category === category) &&
      tool.textContent.toLocaleLowerCase().includes(query);
    tool.hidden = !visible;
    if (visible) count++;
  }
  document.getElementById("tool-status").textContent = `显示 ${count} 个工具`;
  document.getElementById("empty-state").hidden = count !== 0;
}

for (const filter of filters) {
  filter.addEventListener("click", () => {
    category = filter.dataset.filter;
    for (const item of filters) {
      const active = item === filter;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    }
    filterTools();
  });
}
search.addEventListener("input", filterTools);

const tabs = [...document.querySelectorAll('[role="tab"]')];
function selectTab(tab) {
  for (const item of tabs) {
    const selected = item === tab;
    item.setAttribute("aria-selected", String(selected));
    item.tabIndex = selected ? 0 : -1;
    document.getElementById(item.getAttribute("aria-controls")).hidden =
      !selected;
  }
}
for (const tab of tabs) {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (event) => {
    let next;
    const index = tabs.indexOf(tab);
    if (event.key === "ArrowRight") next = tabs[(index + 1) % tabs.length];
    if (event.key === "ArrowLeft")
      next = tabs[(index + tabs.length - 1) % tabs.length];
    if (event.key === "Home") next = tabs[0];
    if (event.key === "End") next = tabs[tabs.length - 1];
    if (next) {
      event.preventDefault();
      selectTab(next);
      next.focus();
    }
  });
}

let toastTimer;
function announce(message) {
  const toast = document.getElementById("toast");
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.hidden = false;
  toastTimer = setTimeout(() => {
    toast.hidden = true;
  }, 4000);
}

async function copyText(value) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch (_) {
      /* Fall back for browser/proxy clipboard restrictions. */
    }
  }
  const previous = document.activeElement;
  const field = document.createElement("textarea");
  field.value = value;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.left = "-9999px";
  document.body.appendChild(field);
  field.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch (_) {
    /* Tell user below. */
  }
  field.remove();
  if (previous instanceof HTMLElement) previous.focus({ preventScroll: true });
  return copied;
}
for (const button of document.querySelectorAll(
  "[data-copy], [data-copy-value]",
)) {
  button.addEventListener("click", async () => {
    const value =
      button.dataset.copyValue ??
      document.getElementById(button.dataset.copy).textContent;
    const copied = await copyText(value);
    announce(
      copied ? "已复制到剪贴板" : "复制未获浏览器允许，请选中文本手动复制",
    );
  });
}
