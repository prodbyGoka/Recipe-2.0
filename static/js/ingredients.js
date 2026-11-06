(() => {
  const input = document.querySelector("#ingredients");
  const chips = document.querySelector("#ingredient-chips");
  if (!input || !chips) return;
  const heroColumns = document.querySelector(".hero-columns");
  const heroArt = document.querySelector(".hero-art");
  const foodImageSearch = document.querySelector("#food-image-search");

  const render = () => {
    const names = input.value.split(",").map((name) => name.trim()).filter(Boolean);
    const unique = [...new Map(names.map((name) => [name.toLocaleLowerCase(), name])).values()];
    chips.replaceChildren(...unique.map((name) => {
      const chip = document.createElement("span");
      chip.className = "ingredient-chip";
      chip.textContent = name;
      return chip;
    }));
    if (heroColumns && heroArt) {
      const enteredIngredients = input.value.split(",").map((name) => name.trim());
      const completedIngredients = enteredIngredients.slice(0, -1).filter((name) => name.length >= 2);
      const finalIngredient = enteredIngredients.at(-1) || "";
      const hasIngredients = completedIngredients.length > 0 || (!input.matches(":focus") && finalIngredient.length >= 2);
      heroColumns.classList.toggle("has-ingredients", hasIngredients);
      heroArt.hidden = !hasIngredients;
    }
    if (foodImageSearch) {
      const url = new URL("https://www.google.com/search");
      url.searchParams.set("tbm", "isch");
      url.searchParams.set("q", `${input.value.trim()} food`);
      foodImageSearch.href = url.toString();
    }
  };
  input.addEventListener("input", render);
  input.addEventListener("blur", render);
  render();
})();
