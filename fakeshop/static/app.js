(() => {
  const sidebar = document.getElementById("app-sidebar");
  const scrim = document.querySelector(".drawer-scrim");
  const opener = document.querySelector("[data-drawer-open]");

  function setDrawer(open) {
    if (!sidebar || !scrim || !opener) return;
    sidebar.classList.toggle("open", open);
    scrim.hidden = !open;
    requestAnimationFrame(() => scrim.classList.toggle("visible", open));
    opener.setAttribute("aria-expanded", String(open));
    document.body.style.overflow = open ? "hidden" : "";
    if (open) sidebar.querySelector("a,button")?.focus();
    else opener.focus();
  }

  function showToast(message, tone = "success") {
    const viewport = document.querySelector(".toast-viewport");
    if (!viewport || !message) return;
    const toast = document.createElement("div");
    toast.className = `toast ${tone}`;
    toast.textContent = message;
    viewport.append(toast);
    requestAnimationFrame(() => toast.classList.add("visible"));
    setTimeout(() => {
      toast.classList.remove("visible");
      setTimeout(() => toast.remove(), 220);
    }, 2600);
  }

  document.addEventListener("click", async (event) => {
    if (event.target.closest("[data-drawer-open]")) setDrawer(true);
    if (event.target.closest("[data-drawer-close]")) setDrawer(false);
    const button = event.target.closest("[data-copy-target]");
    if (!button) return;
    const target = document.getElementById(button.dataset.copyTarget);
    if (!target) return;
    try {
      await navigator.clipboard.writeText(target.textContent.trim());
      showToast(button.dataset.copyMessage || "Copied");
    } catch (_) {
      showToast("The URL could not be copied automatically", "error");
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && sidebar?.classList.contains("open")) setDrawer(false);
  });

  document.addEventListener("submit", (event) => {
    const message = event.target.dataset.confirm;
    if (message && !window.confirm(message)) {
      event.preventDefault();
      return;
    }
    const button = event.submitter;
    if (button && !event.defaultPrevented) {
      button.setAttribute("aria-busy", "true");
      setTimeout(() => button.removeAttribute("aria-busy"), 5000);
    }
  });

  const tabs = [...document.querySelectorAll("[role=tab]")];
  function activateTab(tab) {
    tabs.forEach((item) => {
      const active = item === tab;
      item.setAttribute("aria-selected", String(active));
      item.tabIndex = active ? 0 : -1;
      const panel = document.querySelector(`[data-tab-panel="${item.dataset.tab}"]`);
      if (panel) panel.hidden = !active;
    });
    tab.focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activateTab(tab));
    tab.addEventListener("keydown", (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      let next = index;
      if (event.key === 'ArrowLeft') next = (index + 1) % tabs.length;
      if (event.key === 'ArrowRight') next = (index - 1 + tabs.length) % tabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = tabs.length - 1;
      activateTab(tabs[next]);
    });
  });

  document.body.addEventListener("showToast", (event) => showToast(event.detail?.message, event.detail?.tone));
})();
