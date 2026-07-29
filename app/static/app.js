// Все элементы ищутся здесь, до функций и привязок. Раньше объявления были
// раскиданы по файлу, и верхний блок обращался к const из нижней части —
// на странице добавления это роняло скрипт целиком ещё при загрузке.
const qbClientSelect = document.querySelector("#qb-client-select");
const categoryHint = document.querySelector("#category-hint");
const categorySelect = document.querySelector("#category-select");
const categoryCustom = document.querySelector("#category-custom");
const customBlock = document.querySelector("#category-custom-block");
const savePathInput = document.querySelector("#save-path-input");
const savePathList = document.querySelector("#qb-save-paths");
const langPicker = document.querySelector(".lang-picker");

// Тексты приходят из шаблона: переводы живут в каталоге, а не в скрипте.
const text = (name) => (categoryHint ? categoryHint.dataset[name] || "" : "");
const error = (reason) => text("error").replace("__ERROR__", reason);

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!confirm(form.dataset.confirm)) event.preventDefault();
  });
});

// Своя категория: поля названия и пути показываются только когда она выбрана.
function toggleCustomCategory() {
  if (!categorySelect || !customBlock) return;
  const custom = categorySelect.value === categorySelect.dataset.custom;
  customBlock.hidden = !custom;
  if (custom) categoryCustom.focus();
  else customBlock.querySelectorAll("input").forEach((field) => { field.value = ""; });
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
    // Путь считает сервер: правило «без своего пути → подпапка с именем» живёт там.
    const path = category.effective_path || "";
    option.textContent = path ? `${category.name} — ${path}` : category.name;
    if (path) option.dataset.path = path;
    categorySelect.appendChild(option);
  });
  if (custom) categorySelect.appendChild(custom);
  categorySelect.value = [...categorySelect.options].some((item) => item.value === previous) ? previous : "";
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

if (categorySelect) {
  categorySelect.addEventListener("change", () => {
    toggleCustomCategory();
    showCategoryPath();
  });
  toggleCustomCategory();
  showCategoryPath();
}

if (qbClientSelect && categorySelect && categoryHint) {
  qbClientSelect.addEventListener("change", async () => {
    categoryHint.textContent = text("loading");
    try {
      const response = await fetch(
        `/api/qbittorrent/categories?client_id=${encodeURIComponent(qbClientSelect.value)}`,
        { credentials: "same-origin" },
      );
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
if (langPicker) {
  document.addEventListener("click", (event) => {
    if (!langPicker.contains(event.target)) langPicker.open = false;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") langPicker.open = false;
  });
}
