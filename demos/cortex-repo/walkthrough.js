// Copy buttons for command blocks, and a light/dark toggle.
document.querySelectorAll(".cmd").forEach((block) => {
  const code = block.querySelector("pre code");
  const bar = document.createElement("div");
  bar.className = "bar";
  const label = document.createElement("span");
  label.className = "label";
  label.textContent = block.dataset.label || "Terminal";
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Copy";
  button.addEventListener("click", async () => {
    // Comment lines are for the reader; copying them is harmless in a shell, so keep the text exact.
    await navigator.clipboard.writeText(code.textContent.replace(/\n$/, ""));
    button.textContent = "Copied ✓";
    setTimeout(() => (button.textContent = "Copy"), 1400);
  });
  bar.append(label, button);
  block.prepend(bar);
});

const toggle = document.createElement("button");
toggle.className = "theme";
toggle.type = "button";
toggle.textContent = "◐ Theme";
toggle.addEventListener("click", () => {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
});
document.body.append(toggle);
