document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!confirm(form.dataset.confirm)) event.preventDefault();
  });
});

const qbClientSelect = document.querySelector("#qb-client-select");
const categoryHint = document.querySelector("#category-hint");

// Тексты приходят из шаблона: переводы живут в каталоге, а не в скрипте.
const text = (name) => categoryHint.dataset[name] || "";
const error = (reason) => text("error").replace("__ERROR__", reason);

if (qbClientSelect && categorySelect && categoryHint) {
  qbClientSelect.addEventListener("change", async () => {
    const clientId = qbClientSelect.value;
    categoryHint.textContent = text("loading");
    try {
      const response = await fetch(`/api/qbittorrent/categories?client_id=${encodeURIComponent(clientId)}`, {
        credentials: "same-origin",
      });
      const data = await response.json();
      if (data.status !== "ok") {
        categoryHint.textContent = error(data.error || text("noLink"));
        return;
      }
      rebuildCategories(data.categories);
      rebuildPaths(data.paths || []);
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

// Категория выбирается списком: у <input list> браузер фильтрует подсказки по
// уже введённому тексту, и при заполненном поле в списке оставалась одна строка.
const categorySelect = document.querySelector("#category-select");
const categoryCustom = document.querySelector("#category-custom");
const savePathInput = document.querySelector("#save-path-input");
const savePathList = document.querySelector("#qb-save-paths");

function toggleCustomCategory() {
  if (!categorySelect || !categoryCustom) return;
  const custom = categorySelect.value === categorySelect.dataset.custom;
  categoryCustom.hidden = !custom;
  if (custom) categoryCustom.focus();
  else categoryCustom.value = "";
}

// Папка загрузки и категория не независимы: пустое поле означает «взять путь
// у категории». Показываем это плейсхолдером, чтобы никто не вписал путь
// вслепую и молча не перебил категорию.
function showCategoryPath() {
  if (!categorySelect || !savePathInput) return;
  const chosen = categorySelect.selectedOptions[0];
  const path = chosen && chosen.dataset.path;
  savePathInput.placeholder = path
    ? savePathInput.dataset.fromCategory.replace("__PATH__", path)
    : savePathInput.dataset.clientDefault;
}

if (categorySelect) {
  categorySelect.addEventListener("change", () => {
    toggleCustomCategory();
    showCategoryPath();
  });
  toggleCustomCategory();
  showCategoryPath();
}

// При смене клиента список категорий и подсказки путей перестраиваются:
// у каждого клиента они свои. Выбранную категорию сохраняем, если она есть и там.
function rebuildCategories(categories) {
  if (!categorySelect) return;
  const previous = categorySelect.value;
  const custom = categorySelect.querySelector(`option[value="${categorySelect.dataset.custom}"]`);
  const none = categorySelect.querySelector('option[value=""]');
  categorySelect.replaceChildren();
  if (none) categorySelect.appendChild(none);
  categories.forEach((category) => {
    const option = document.createElement("option");
    option.value = category.name;
    option.textContent = category.save_path ? `${category.name} — ${category.save_path}` : category.name;
    if (category.save_path) option.dataset.path = category.save_path;
    categorySelect.appendChild(option);
  });
  if (custom) categorySelect.appendChild(custom);
  categorySelect.value = [...categorySelect.options].some((o) => o.value === previous) ? previous : "";
  toggleCustomCategory();
}

function rebuildPaths(paths) {
  if (!savePathList || !savePathInput) return;
  savePathList.replaceChildren();
  paths.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.path;
    option.label = item.kind === "default"
      ? savePathInput.dataset.labelDefault
      : savePathInput.dataset.labelCategory.replace("__NAME__", item.category);
    savePathList.appendChild(option);
  });
}
