document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!confirm(form.dataset.confirm)) event.preventDefault();
  });
});

const qbClientSelect = document.querySelector("#qb-client-select");
const categoryList = document.querySelector("#qb-categories");
const categoryHint = document.querySelector("#category-hint");

// Тексты приходят из шаблона: переводы живут в каталоге, а не в скрипте.
const text = (name) => categoryHint.dataset[name] || "";
const error = (reason) => text("error").replace("__ERROR__", reason);

if (qbClientSelect && categoryList && categoryHint) {
  qbClientSelect.addEventListener("change", async () => {
    const clientId = qbClientSelect.value;
    categoryHint.textContent = text("loading");
    categoryList.replaceChildren();
    try {
      const response = await fetch(`/api/qbittorrent/categories?client_id=${encodeURIComponent(clientId)}`, {
        credentials: "same-origin",
      });
      const data = await response.json();
      if (data.status !== "ok") {
        categoryHint.textContent = error(data.error || text("noLink"));
        return;
      }
      data.categories.forEach((category) => {
        const option = document.createElement("option");
        option.value = category.name;
        if (category.save_path) option.label = category.save_path;
        categoryList.appendChild(option);
      });
      categoryHint.textContent = data.categories.length ? text("loaded") : text("empty");
      showCategoryPath();
    } catch (failure) {
      categoryHint.textContent = error(failure);
    }
  });
}

// Выпадающий выбор языка закрывается по клику мимо и по Escape:
// <details> сам этого не делает, а открытое меню в шапке мешает.
const langPicker = document.querySelector(".lang-picker");
if (langPicker) {
  document.addEventListener("click", (event) => {
    if (!langPicker.contains(event.target)) langPicker.open = false;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") langPicker.open = false;
  });
}

// Папка загрузки и категория не независимы: пустое поле означает «взять путь
// у категории». Показываем это плейсхолдером, чтобы никто не вписал путь
// вслепую и молча не перебил категорию.
const categoryInput = document.querySelector("#category-input");
const savePathInput = document.querySelector("#save-path-input");

function showCategoryPath() {
  if (!categoryInput || !savePathInput) return;
  const chosen = [...(categoryList ? categoryList.options : [])]
    .find((option) => option.value === categoryInput.value.trim());
  const path = chosen && chosen.label;
  savePathInput.placeholder = path
    ? savePathInput.dataset.fromCategory.replace("__PATH__", path)
    : savePathInput.dataset.clientDefault;
}

if (categoryInput && savePathInput) {
  categoryInput.addEventListener("input", showCategoryPath);
  categoryInput.addEventListener("change", showCategoryPath);
  showCategoryPath();
}
