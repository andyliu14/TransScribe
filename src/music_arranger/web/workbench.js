"use strict";
const el = (id) => document.getElementById(id);
const modes = [...document.querySelectorAll("[data-mode]")];
let options,
  mode = "example",
  sourceId = null,
  pdmxSource = null,
  busy = false;
let query = "",
  offset = 0,
  nextOffset = null,
  searchTicket = 0;
async function api(path, params = {}, post = false, body = null) {
  const response = await fetch(path + "?" + new URLSearchParams(params), {
    method: post ? "POST" : "GET",
    headers: post ? { "X-Arranger": "workbench" } : {},
    body,
  });
  if (!response.ok) {
    const msg = await response.text();
    let detail;
    try {
      detail = JSON.parse(msg).error;
    } catch {}
    throw Error(detail || (msg.length < 180 ? msg : response.statusText));
  }
  return response.json();
}
function controls() {
  el("build").disabled = busy || !options || !(mode === "example" || sourceId);
  for (const control of [
    ...modes,
    el("ensemble"),
    el("variant"),
    el("notation"),
    el("pdf"),
  ])
    control.disabled = busy;
  el("catalog").inert = busy;
}
function variants() {
  const target = options[el("ensemble").value];
  el("variant").replaceChildren(
    ...target.variants.map((v) => new Option(v.replaceAll("-", " "), v)),
  );
  el("variant").value = target.default;
}
function selectMode(value) {
  mode = value;
  sourceId = null;
  pdmxSource = null;
  el("original").hidden = true;
  el("status").textContent = "";
  el("notation").value = "";
  el("selected").textContent =
    value === "example"
      ? "Courtyard Round · violin and viola"
      : "Choose a source score.";
  for (const btn of modes) {
    el(btn.dataset.mode).hidden = btn.dataset.mode !== value;
    btn.setAttribute("aria-pressed", String(btn.dataset.mode === value));
  }
  for (const btn of el("results").children)
    btn.setAttribute("aria-pressed", "false");
  controls();
}
async function importSource() {
  const file = el("notation").files[0];
  const params =
    mode === "catalog"
      ? { mode: "pdmx", source: pdmxSource }
      : mode === "upload"
        ? { mode: "file", name: file.name }
        : { mode: "example" };
  const data = await api(
    "/api/source",
    params,
    true,
    mode === "upload" ? file : null,
  );
  sourceId = data.id;
  el("selected").textContent =
    data.title +
    " · " +
    data.parts
      .map((p) => (p.name || "Part") + " (" + p.measures + " bars)")
      .join(", ");
  el("original").href = "/files/" + sourceId + "/original";
  el("original").hidden = false;
}
async function loadSource() {
  sourceId = null;
  el("original").hidden = true;
  busy = true;
  controls();
  el("selected").textContent = "Importing and inspecting score…";
  el("status").textContent = "";
  try {
    await importSource();
  } catch (e) {
    el("status").textContent = e.message;
    el("selected").textContent = "Choose a source score.";
  } finally {
    busy = false;
    controls();
  }
}
async function search(page) {
  const ticket = ++searchTicket;
  el("results").replaceChildren();
  el("prev").hidden = el("next").hidden = true;
  el("search-status").textContent = "Searching catalog…";
  try {
    const data = await api("/api/search", { q: query, offset: page }, true);
    if (ticket !== searchTicket) return;
    offset = page;
    nextOffset = data.next_offset;
    el("search-status").textContent = data.results.length
      ? "Select an edition to import."
      : "No matching editions.";
    for (const row of data.results) {
      const btn = document.createElement("button");
      btn.className = "result";
      btn.textContent = row.title || row.song_name;
      const detail = document.createElement("small");
      detail.textContent = [
        row.composer,
        row.instruments.join(", "),
        row.n_tracks + " tracks",
        row.license,
        row.license_conflict === true ? "license conflict reported" : null,
      ]
        .filter(Boolean)
        .join(" · ");
      btn.append(detail);
      btn.setAttribute("aria-pressed", String(pdmxSource === row.source));
      btn.onclick = async () => {
        pdmxSource = row.source;
        for (const other of el("results").children)
          other.setAttribute("aria-pressed", String(other === btn));
        await loadSource();
      };
      el("results").append(btn);
    }
    el("prev").hidden = offset === 0;
    el("next").hidden = nextOffset === null;
  } catch (e) {
    if (ticket === searchTicket) el("search-status").textContent = e.message;
  }
}
async function history() {
  const rows = await api("/api/history");
  el("history").replaceChildren();
  if (!rows.length) {
    const p = document.createElement("p");
    p.textContent = "Completed packets will appear here.";
    el("history").append(p);
  }
  for (const r of rows) {
    const card = document.createElement("article");
    card.className = "edition";
    const label = document.createElement("div");
    label.textContent = r.name;
    const info = document.createElement("small");
    info.textContent = [
      new Date(r.saved * 1000).toLocaleString(),
      r.ensemble,
      r.candidate,
      r.eligible ? "Structural checks passed" : "Review flagged issues",
    ].join(" · ");
    label.append(info);
    const links = document.createElement("div");
    links.className = "links";
    for (const [asset, text] of [
      ["zip", "Download packet"],
      ["score", "Score PDF"],
      ["audit", "Evaluation"],
    ]) {
      if (asset === "score" && !r.rendered) continue;
      const a = document.createElement("a");
      a.href = "/files/" + r.id + "/" + asset;
      a.textContent = text;
      links.append(a);
    }
    const copy = document.createElement("button");
    copy.className = "copy";
    copy.textContent = "Copy review brief";
    copy.onclick = async () => {
      const url = "/files/" + r.id + "/brief";
      try {
        const response = await fetch(url);
        if (!response.ok) throw Error(response.statusText);
        await navigator.clipboard.writeText(await response.text());
        copy.textContent = "Brief copied";
      } catch {
        const a = document.createElement("a");
        a.href = url;
        a.textContent = "Open review brief";
        links.append(a);
        copy.remove();
      }
    };
    links.append(copy);
    card.append(label, links);
    el("history").append(card);
  }
}
for (const btn of modes) btn.onclick = () => selectMode(btn.dataset.mode);
el("ensemble").onchange = variants;
el("lookup").onsubmit = (e) => {
  e.preventDefault();
  query = el("query").value;
  search(0);
};
el("prev").onclick = () => search(Math.max(0, offset - 8));
el("next").onclick = () => search(nextOffset);
el("notation").onchange = () => {
  if (el("notation").files.length) loadSource();
};
el("build").onclick = async () => {
  busy = true;
  controls();
  try {
    el("status").textContent = "Preparing source…";
    if (!sourceId) await importSource();
    el("status").textContent = "Building and evaluating both editions…";
    await api(
      "/api/packet",
      {
        source: sourceId,
        ensemble: el("ensemble").value,
        variant: el("variant").value,
        pdf: el("pdf").checked,
      },
      true,
    );
    await history();
    el("status").textContent = "Packet ready below.";
  } catch (e) {
    el("status").textContent = e.message;
  } finally {
    busy = false;
    controls();
  }
};
(async () => {
  try {
    const [ensembles, capabilities] = await Promise.all([api("/api/options"), api("/api/capabilities")]);
    options = ensembles;
    if (!capabilities.pdf) {
      el("pdf").checked = false;
      el("pdf").closest("label").hidden = true;
      el("print-help").hidden = false;
    }
    variants();
    controls();
    await history();
  } catch (e) {
    el("status").textContent = e.message;
  }
})();
