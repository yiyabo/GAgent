"use strict";

// Everything stays in the browser: this page never accepts tokens or calls MCP.
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const entrances = new Map();

// Only animate opacity and transforms, never the scroll position or layout.
// Content remains visible if animations or IntersectionObserver are unavailable.
function enter(elements, duration = 240, distance = 8) {
  if (reducedMotion.matches) return;
  elements.forEach((element, index) => {
    if (!element.animate) return;
    entrances.get(element)?.cancel();
    const animation = element.animate(
      [
        { opacity: 0, transform: `translateY(${distance}px)` },
        { opacity: 1, transform: "translateY(0)" },
      ],
      {
        duration,
        delay: Math.min(index, 4) * 20,
        easing: "cubic-bezier(0.22, 1, 0.36, 1)",
        fill: "backwards",
      },
    );
    entrances.set(element, animation);
    const cleanup = () => {
      if (entrances.get(element) === animation) entrances.delete(element);
    };
    animation.addEventListener("finish", cleanup, { once: true });
    animation.addEventListener("cancel", cleanup, { once: true });
  });
}
reducedMotion.addEventListener("change", () => {
  if (reducedMotion.matches) {
    for (const animation of entrances.values()) animation.cancel();
    entrances.clear();
  }
});

const filters = [...document.querySelectorAll("[data-filter]")];
const tools = [...document.querySelectorAll(".tool[data-category]")];
const toolText = new Map(
  tools.map((tool) => [tool, tool.textContent.toLocaleLowerCase()]),
);
const search = document.getElementById("tool-search");
let category = "all";

function filterTools() {
  const query = search.value.trim().toLocaleLowerCase();
  const entering = [];
  let count = 0;
  for (const tool of tools) {
    const visible =
      (category === "all" || tool.dataset.category === category) &&
      toolText.get(tool).includes(query);
    if (visible && tool.hidden) entering.push(tool);
    tool.hidden = !visible;
    if (visible) count++;
  }
  document.getElementById("tool-status").textContent = `显示 ${count} 个工具`;
  document.getElementById("empty-state").hidden = count !== 0;
  enter(entering);
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
  if (tab.getAttribute("aria-selected") === "true") return;
  for (const item of tabs) {
    const selected = item === tab;
    item.setAttribute("aria-selected", String(selected));
    item.tabIndex = selected ? 0 : -1;
    document.getElementById(item.getAttribute("aria-controls")).hidden =
      !selected;
  }
  enter([document.getElementById(tab.getAttribute("aria-controls"))]);
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
  const label = button.textContent;
  let feedbackTimer;
  button.addEventListener("click", async () => {
    const value =
      button.dataset.copyValue ??
      document.getElementById(button.dataset.copy).textContent;
    const copied = await copyText(value);
    clearTimeout(feedbackTimer);
    button.classList.toggle("is-copied", copied);
    if (button.classList.contains("copy-button")) {
      button.textContent = copied ? "已复制" : label;
    }
    feedbackTimer = setTimeout(() => {
      button.classList.remove("is-copied");
      button.textContent = label;
    }, 2000);
    announce(
      copied ? "已复制到剪贴板" : "复制未获浏览器允许，请选中文本手动复制",
    );
  });
}

if ("IntersectionObserver" in window) {
  const reveals = new IntersectionObserver(
    (entries, observer) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        enter([entry.target], 440, 14);
        observer.unobserve(entry.target);
      }
    },
    { threshold: 0.08 },
  );
  for (const section of document.querySelectorAll(
    ".capability, .workflow-layout, .section-heading, .connect-layout, .faq-section, .closing-inner",
  )) {
    reveals.observe(section);
  }

  const links = [
    ...document.querySelectorAll('.header-inner nav a[href^="#"]'),
  ];
  const navigation = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        for (const link of links) {
          if (link.hash === `#${entry.target.id}`) {
            link.setAttribute("aria-current", "location");
          } else {
            link.removeAttribute("aria-current");
          }
        }
      }
    },
    { rootMargin: "-15% 0px -65% 0px" },
  );
  for (const link of links) {
    const target = document.querySelector(link.hash);
    if (target) navigation.observe(target);
  }
}
