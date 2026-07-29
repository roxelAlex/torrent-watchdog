document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!confirm(form.dataset.confirm)) event.preventDefault();
  });
});

const qbClientSelect = document.querySelector("#qb-client-select");
const categoryList = document.querySelector("#qb-categories");
const categoryHint = document.querySelector("#category-hint");

if (qbClientSelect && categoryList && categoryHint) {
  qbClientSelect.addEventListener("change", async () => {
    const clientId = qbClientSelect.value;
    categoryHint.textContent = "Загружаю категории выбранного клиента...";
    categoryList.replaceChildren();
    try {
      const response = await fetch(`/api/qbittorrent/categories?client_id=${encodeURIComponent(clientId)}`, {
        credentials: "same-origin",
      });
      const data = await response.json();
      if (data.status !== "ok") {
        categoryHint.textContent = `Категории не загружены: ${data.error || "нет связи с клиентом"}`;
        return;
      }
      data.categories.forEach((category) => {
        const option = document.createElement("option");
        option.value = category.name;
        if (category.save_path) option.label = category.save_path;
        categoryList.appendChild(option);
      });
      categoryHint.textContent = data.categories.length
        ? "Категории загружены из выбранного клиента."
        : "В выбранном клиенте нет категорий или список пуст.";
    } catch (error) {
      categoryHint.textContent = `Категории не загружены: ${error}`;
    }
  });
}
