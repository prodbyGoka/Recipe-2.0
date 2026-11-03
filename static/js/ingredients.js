(() => {
  const input = document.querySelector("#ingredients");
  const chips = document.querySelector("#ingredient-chips");
  if (!input || !chips) return;

  const render = () => {
    const names = input.value.split(",").map((name) => name.trim()).filter(Boolean);
    const unique = [...new Map(names.map((name) => [name.toLocaleLowerCase(), name])).values()];
    chips.replaceChildren(...unique.map((name) => {
      const chip = document.createElement("span");
      chip.className = "ingredient-chip";
      chip.textContent = name;
      return chip;
    }));
  };
  input.addEventListener("input", render);
  render();
})();
