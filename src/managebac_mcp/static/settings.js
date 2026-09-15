"use strict";
const q = id => document.getElementById(id);
let state = {accounts: [], default_id: null};
let busy = false;
let deletingAccountId = null;
const returnTo = new URLSearchParams(location.search).get("return_to");
try {
  const url = new URL(returnTo);
  if (url.origin === "https://mcp.archik.tech" && url.pathname.startsWith("/connections/") && !url.username && !url.password) {
    q("return").href = url.href;
    q("return").textContent = "Продолжить в Life OS";
  }
} catch (_) { /* Direct visits use the normal Life OS link. */ }

async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {"Content-Type": "application/json"}});
  const value = await response.json();
  if (!response.ok) throw Error(value.error || "Не удалось выполнить запрос");
  return value;
}
function message(text, error = false) {
  q("status").textContent = text;
  q("status").className = "status " + (error ? "error" : "success");
}
function edit(account) {
  q("form").reset();
  q("account-id").value = account?.id || "";
  q("label").value = account?.label || "";
  q("username").value = account?.username || "";
  q("school").value = account?.school || state.school || "";
  q("password").required = true;
  q("form-title").textContent = account ? "Переподключить аккаунт" : "Добавить аккаунт";
  q("form-status").textContent = "";
  q("dialog").showModal();
}
function button(label, handler, className = "") {
  const result = document.createElement("button");
  result.textContent = label;
  result.className = className;
  result.disabled = busy;
  result.onclick = handler;
  return result;
}
async function mutate(path, options, success, accountId = null) {
  if (busy) return;
  busy = true;
  deletingAccountId = accountId;
  if (accountId) message("Удаляем аккаунт…");
  render();
  try { state = {...state, ...await api(path, options)}; message(success); }
  catch (error) { message(error.message, true); }
  finally { busy = false; deletingAccountId = null; render(); }
}
function render() {
  const list = q("list");
  list.replaceChildren();
  q("add").disabled = busy;
  if (!state.accounts.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Пока нет аккаунтов. Добавьте школьный аккаунт, чтобы агент получил доступ к заданиям и расписанию.";
    list.append(empty);
  }
  for (const account of state.accounts) {
    const row = document.createElement("div"); row.className = "row";
    row.setAttribute("aria-busy", String(deletingAccountId === account.id));
    const info = document.createElement("div"); info.className = "info";
    const title = document.createElement("strong"); title.textContent = account.label;
    if (account.id === state.default_id) {
      const badge = document.createElement("span"); badge.className = "badge"; badge.textContent = "Основной"; title.append(badge);
    }
    const details = document.createElement("small"); details.textContent = account.username + " · " + new URL(account.school).hostname;
    info.append(title, details);
    const actions = document.createElement("div"); actions.className = "actions";
    if (account.id !== state.default_id) actions.append(button("Сделать основным", () => mutate("/settings/api/default", {method: "POST", body: JSON.stringify({id: account.id})}, "Основной аккаунт изменён")));
    actions.append(button("Переподключить", () => edit(account)));
    actions.append(button(deletingAccountId === account.id ? "Удаляем…" : "Удалить", () => {
      if (confirm(`Удалить аккаунт «${account.label}»? Его пароль и локальные учебные данные будут удалены с сервера. Данные в ManageBac останутся без изменений.`)) {
        mutate("/settings/api/accounts/" + encodeURIComponent(account.id), {method: "DELETE"}, "Аккаунт удалён", account.id);
      }
    }, "danger"));
    row.append(info, actions); list.append(row);
  }
}
q("add").onclick = () => edit();
q("cancel").onclick = () => q("dialog").close();
q("dialog").addEventListener("close", () => { q("password").value = ""; });
q("form").onsubmit = async event => {
  event.preventDefault();
  if (busy) return;
  busy = true;
  q("save").disabled = q("cancel").disabled = true;
  q("form-status").className = "status";
  q("form-status").textContent = "Проверяем вход в школу… Это может занять до минуты.";
  try {
    const payload = Object.fromEntries(["label", "school", "username", "password"].map(id => [id, q(id).value]));
    if (q("account-id").value) payload.id = q("account-id").value;
    state = {...state, ...await api("/settings/api/accounts", {method: "POST", body: JSON.stringify(payload)})};
    q("dialog").close();
    message("Аккаунт подключён. Продолжите в Life OS, чтобы проверить доступ и загрузить инструменты.");
  } catch (error) {
    q("form-status").className = "status error";
    q("form-status").textContent = error.message;
  } finally {
    q("password").value = "";
    busy = false;
    q("save").disabled = q("cancel").disabled = false;
    render();
  }
};
api("/settings/api/accounts").then(value => { state = value; render(); }).catch(error => { q("list").replaceChildren(); message(error.message, true); });
