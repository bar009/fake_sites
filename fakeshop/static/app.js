document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy-target]");
  if (!button) return;
  const target = document.getElementById(button.dataset.copyTarget);
  if (!target) return;
  await navigator.clipboard.writeText(target.textContent.trim());
  const original = button.textContent;
  button.textContent = "הועתק";
  setTimeout(() => { button.textContent = original; }, 1200);
});

document.addEventListener("submit", (event) => {
  const message = event.target.dataset.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
});
