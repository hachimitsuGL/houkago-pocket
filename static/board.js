"use strict";

const cardList = document.querySelector("#cards");
const count = document.querySelector("#post-count");
const empty = document.querySelector("#empty");
const filterResult = document.querySelector("#filter-result");
const form = document.querySelector("#post-form");
const submitButton = document.querySelector("#submit");
const formError = document.querySelector("#form-error");
const successNote = document.querySelector("#success-note");
const loadNote = document.querySelector("#load-note");
const composeDialog = document.querySelector("#compose");
const categories = new Map([["勉強", "study"], ["部活", "club"], ["息抜き", "rest"]]);
const durations = new Set([5, 15, 30]);
const knownPosts = new Map();
const dateFormat = new Intl.DateTimeFormat("ja-JP", { year: "numeric", month: "short", day: "numeric" });
let submitting = false;

function updateFilters() {
  const time = document.querySelector('input[name="available"]:checked').value;
  const category = document.querySelector('input[name="category-filter"]:checked').value;
  let visible = 0;
  for (const card of cardList.children) {
    const matches = (time === "all" || Number(card.dataset.minutes) <= Number(time))
      && (category === "all" || category === card.dataset.category);
    card.hidden = !matches;
    if (matches) visible++;
  }
  count.textContent = visible + " 件";
  filterResult.textContent = visible + "件のアイデアを表示しています。";
  empty.hidden = visible > 0;
}

function clearFilters() {
  document.querySelector('input[name="available"][value="all"]').checked = true;
  document.querySelector('input[name="category-filter"][value="all"]').checked = true;
  updateFilters();
}

document.querySelectorAll('.time-filter, .category-filter').forEach(group => {
  group.addEventListener("change", updateFilters);
});
document.querySelector("#clear-filters").addEventListener("click", () => {
  clearFilters();
  document.querySelector('input[name="category-filter"]:checked').focus();
});

document.querySelectorAll("[data-open]").forEach(button => {
  button.addEventListener("click", () => {
    const dialog = document.getElementById(button.dataset.open);
    dialog.showModal();
    if (dialog === composeDialog) form.elements.title.focus();
  });
});
document.querySelectorAll("[data-close]").forEach(button => {
  button.addEventListener("click", () => button.closest("dialog").close());
});

function element(tag, className, value) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (value !== undefined) result.textContent = value;
  return result;
}

function makeCard(post) {
  const categoryClass = categories.get(post.category);
  const card = element("article", "card");
  card.dataset.category = post.category;
  card.dataset.minutes = String(post.minutes);
  const meta = element("div", "card-meta");
  const category = element("span", "category " + categoryClass);
  category.append(element("i"), document.createTextNode(post.category));
  const time = element("span", "time");
  time.append(element("i", "clock-icon"), document.createTextNode(post.minutes + " 分"));
  meta.append(category, time);
  card.append(meta, element("h3", null, post.title), element("p", "card-body", post.body));

  if (typeof post.imageUrl === "string" && post.imageUrl.startsWith("/api/images/")) {
    const url = new URL(post.imageUrl, window.location.origin);
    if (url.origin === window.location.origin && url.pathname.startsWith("/api/images/")) {
      const image = element("img", "card-image");
      image.alt = post.title + "の投稿写真";
      image.loading = "lazy";
      image.addEventListener("error", () => {
        image.replaceWith(element("p", "image-error", "投稿写真を読み込めませんでした。"));
      });
      image.src = url.href;
      card.append(image);
    }
  }
  const byline = element("div", "byline");
  const avatar = element("i", "guest-avatar");
  avatar.setAttribute("aria-hidden", "true");
  byline.append(avatar, element("span", null, post.alias || "匿名さん"));
  const created = new Date(post.createdAt);
  if (!Number.isNaN(created.getTime())) {
    const date = element("time", null, dateFormat.format(created));
    date.dateTime = created.toISOString();
    byline.append(date);
  }
  card.append(byline);
  return card;
}

function remember(post) {
  if (!post || typeof post.id !== "string" || typeof post.title !== "string"
      || typeof post.body !== "string" || !categories.has(post.category)
      || !durations.has(post.minutes)) return false;
  knownPosts.set(post.id, post);
  return true;
}

function renderPosts() {
  for (const card of cardList.querySelectorAll("[data-post]")) card.remove();
  const sorted = [...knownPosts.values()].sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
  const fragment = document.createDocumentFragment();
  for (const post of sorted.slice(0, 100)) {
    const card = makeCard(post);
    card.dataset.post = post.id;
    fragment.append(card);
  }
  cardList.prepend(fragment);
  updateFilters();
}

async function loadPosts() {
  try {
    const response = await fetch("/api/posts", { headers: { "Accept": "application/json" }, cache: "no-store" });
    if (!response.ok) throw new Error();
    const data = await response.json();
    if (!Array.isArray(data.posts)) throw new Error();
    data.posts.forEach(remember);
    renderPosts();
  } catch {
    loadNote.textContent = "みんなの投稿を読み込めませんでした。編集部の見本は引き続き読めます。";
    loadNote.hidden = false;
  }
}

function validateImage(input) {
  const image = input.files[0];
  let message = "";
  if (image && image.size > 3 * 1024 * 1024) {
    message = "写真は3 MB以下のものを選んでください。";
  } else if (image && image.type && !["image/jpeg", "image/png", "image/webp"].includes(image.type)) {
    message = "JPEG、PNG、WebPの写真を選んでください。";
  }
  input.setCustomValidity(message);
}
form.elements.image.addEventListener("change", event => validateImage(event.target));
for (const name of ["title", "body"]) {
  form.elements[name].addEventListener("input", event => {
    event.target.setCustomValidity(event.target.value.trim() ? "" : "内容を入力してください。");
  });
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (submitting) return;
  validateImage(form.elements.image);
  if (!form.reportValidity()) return;
  const data = new FormData(form);
  if (!form.elements.image.files.length) data.delete("image");
  submitting = true;
  submitButton.disabled = true;
  submitButton.textContent = "投稿中…";
  formError.hidden = true;
  successNote.hidden = true;
  try {
    const response = await fetch("/api/posts", { method: "POST", body: data, headers: { "Accept": "application/json" } });
    const payload = await response.json().catch(() => null);
    if (response.status !== 201 || !remember(payload?.post)) {
      throw new Error(typeof payload?.error === "string" ? payload.error : "投稿を確認できませんでした。掲示板を確認してからもう一度お試しください。");
    }
    form.reset();
    clearFilters();
    renderPosts();
    composeDialog.close();
    successNote.textContent = "アイデアを投稿しました。掲示板の先頭で確認できます。";
    successNote.hidden = false;
    successNote.tabIndex = -1;
    successNote.focus();
  } catch (error) {
    formError.textContent = error instanceof TypeError ? "通信できませんでした。接続と掲示板を確認してからもう一度お試しください。" : error.message;
    formError.hidden = false;
  } finally {
    submitting = false;
    submitButton.disabled = false;
    submitButton.textContent = "掲示板に投稿する";
  }
});

loadPosts();
