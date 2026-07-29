// Скрипт должен загружаться без ошибок и при существующих элементах страницы.
// Именно это когда-то и сломалось: обращение к const до его объявления роняло
// весь файл на странице добавления, а syntax-check такое не ловит.
const element = (name) => ({
  name, dataset: {}, options: [], selectedOptions: [], hidden: false, value: "",
  addEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
  replaceChildren() {}, appendChild() {}, focus() {}, contains: () => false,
});

globalThis.document = {
  querySelector: () => element("any"),
  querySelectorAll: () => [],
  addEventListener: () => {},
  createElement: () => element("new"),
};

try {
  require(process.argv[2]);
  console.log("ok");
} catch (failure) {
  console.error(`${failure.constructor.name}: ${failure.message}`);
  process.exitCode = 1;
}
