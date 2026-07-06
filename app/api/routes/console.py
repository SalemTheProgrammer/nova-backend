"""Console Simulateur : page HTML autonome (aucune dépendance frontend).

Sert un pupitre de pilotage complet du simulateur machine — mêmes actions que
l'ancien panneau React (`/simulateur`), mais rendu côté serveur en HTML/JS
vanilla. La page consomme directement l'API JSON existante
(`/api/v1/simulateur/...`, `/api/v1/machines`, `/api/v1/ordres-fabrication`)
et écoute `/ws/dashboard` pour les mises à jour temps réel — aucun nouvel
endpoint métier n'était nécessaire.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.core.config import get_settings

router = APIRouter(tags=["console"])


_PAGE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Nova — Console Usine</title>
<style>
  :root{
    --bg:#f7f8fa; --card:#ffffff; --border:#e5e7eb; --text:#111827; --muted:#6b7280;
    --primary:#111827; --primary-fg:#ffffff;
    --green:#059669; --green-bg:#05966915; --amber:#d97706; --amber-bg:#d9770615;
    --red:#dc2626; --red-bg:#dc262615; --blue:#2563eb; --blue-bg:#2563eb15;
    --radius:12px;
  }
  *{box-sizing:border-box;}
  body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,Segoe UI,Inter,system-ui,sans-serif;}
  .wrap{max-width:1400px;margin:0 auto;padding:20px 24px 40px;}
  header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px;flex-wrap:wrap;}
  h1{font-size:18px;margin:0;font-weight:700;}
  p.sub{margin:2px 0 0;color:var(--muted);font-size:12.5px;}
  .pill{display:inline-flex;align-items:center;gap:6px;padding:5px 10px;border-radius:999px;font-size:12px;font-weight:600;}
  .pill.on{background:var(--green-bg);color:var(--green);}
  .pill.off{background:var(--red-bg);color:var(--red);}
  .dot{width:7px;height:7px;border-radius:999px;background:currentColor;}
  .card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:16px;}
  .grid{display:grid;gap:16px;}
  .cols-2{grid-template-columns:1.15fr 1fr;}
  @media(max-width:980px){.cols-2{grid-template-columns:1fr;}}
  .row{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;}
  label.field{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted);font-weight:600;}
  select,input[type=text],input[type=number]{
    height:34px;border:1px solid var(--border);border-radius:8px;padding:0 10px;font-size:13px;background:#fff;color:var(--text);
  }
  select{min-width:200px;}
  h3{font-size:13px;margin:0 0 10px;font-weight:700;}
  .muted{color:var(--muted);}
  button{
    cursor:pointer;border-radius:9px;border:1px solid var(--border);background:#fff;color:var(--text);
    font-size:13px;font-weight:600;padding:0 14px;height:34px;display:inline-flex;align-items:center;gap:6px;
    transition:filter .12s;
  }
  button:hover:not(:disabled){filter:brightness(0.97);}
  button:disabled{opacity:.45;cursor:not-allowed;}
  button.primary{background:var(--primary);color:var(--primary-fg);border-color:var(--primary);}
  button.danger{background:var(--red-bg);color:var(--red);border-color:transparent;}
  button.big{height:52px;font-size:14px;flex:1;justify-content:center;}
  .btn-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;}
  @media(max-width:560px){.btn-grid{grid-template-columns:repeat(2,1fr);}}
  .scenario{
    text-align:left;border-radius:10px;border:1px solid var(--border);background:#fff;padding:10px 12px;
    height:auto;flex-direction:column;align-items:flex-start;gap:2px;font-weight:600;
  }
  .scenario small{font-weight:400;color:var(--muted);white-space:normal;line-height:1.35;}
  .scenarios{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;}
  @media(max-width:780px){.scenarios{grid-template-columns:1fr;}}
  .state{display:flex;align-items:center;gap:22px;flex-wrap:wrap;}
  .badge{
    display:flex;flex-direction:column;align-items:center;gap:4px;border:2px solid var(--border);
    border-radius:14px;padding:14px 20px;min-width:150px;
  }
  .badge .ic{font-size:26px;line-height:1;}
  .badge .lbl{font-size:15px;font-weight:700;}
  .badge .sub{font-size:12px;opacity:.8;}
  .stat{text-align:center;}
  .stat .num{font-size:22px;font-weight:800;}
  .stat .lbl{font-size:11px;color:var(--muted);}
  .downtime-banner{margin-top:14px;border-radius:10px;border:1px solid var(--red);background:var(--red-bg);color:var(--red);padding:9px 14px;font-size:12.5px;text-align:center;}
  .section-title{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:700;margin:14px 0 8px;}
  .range-row{display:flex;flex-direction:column;gap:6px;}
  details{border-top:1px solid var(--border);margin-top:14px;padding-top:10px;}
  summary{cursor:pointer;font-size:12.5px;font-weight:600;color:var(--muted);}
  .log{height:340px;overflow-y:auto;border:1px solid var(--border);border-radius:10px;}
  .log .row-item{padding:8px 12px;border-bottom:1px solid var(--border);font-size:12.5px;}
  .log .row-item:last-child{border-bottom:none;}
  .log .row-item .top{display:flex;justify-content:space-between;font-weight:600;}
  .log .row-item .time{color:var(--muted);font-weight:400;}
  .log .row-item .payload{margin-top:2px;color:var(--muted);font-family:ui-monospace,Consolas,monospace;font-size:11px;}
  .empty{padding:32px 12px;text-align:center;color:var(--muted);font-size:12.5px;}
  .toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:var(--text);color:#fff;
    padding:10px 18px;border-radius:10px;font-size:13px;box-shadow:0 6px 20px rgba(0,0,0,.18);opacity:0;
    transition:opacity .2s;pointer-events:none;z-index:50;max-width:520px;text-align:center;}
  .toast.show{opacity:1;}
  .toast.error{background:var(--red);}
  .tag-list{margin-top:8px;font-family:ui-monospace,Consolas,monospace;font-size:11.5px;color:var(--muted);}

  /* --- Canvas de flux (façon n8n) : lignes = nœuds, liens = flux matière --- */
  .flux-wrap{position:relative;overflow-x:auto;}
  #fluxSvg{display:block;min-width:100%;}
  .flux-node{cursor:pointer;}
  .flux-node rect.body{fill:#fff;stroke:var(--border);stroke-width:1.5;rx:12;transition:stroke .15s;}
  .flux-node.selected rect.body{stroke:var(--blue);stroke-width:2.5;}
  .flux-node text{font-family:inherit;}
  .flux-node .titre{font-size:13px;font-weight:700;fill:var(--text);}
  .flux-node .sous{font-size:10.5px;fill:var(--muted);}
  .flux-node .trs{font-size:11px;font-weight:700;}
  .flux-edge{fill:none;stroke:var(--blue);stroke-width:2;stroke-dasharray:7 6;
    animation:flux-dash .7s linear infinite;cursor:pointer;}
  .flux-edge:hover{stroke:var(--red);stroke-width:3;}
  .flux-edge-hit{fill:none;stroke:transparent;stroke-width:14;cursor:pointer;}
  @keyframes flux-dash{to{stroke-dashoffset:-13;}}
  .flux-bar{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;
    padding:12px 16px;border-bottom:1px solid var(--border);}
  .flux-bar h3{margin:0;flex:1;min-width:160px;}
  .articles-panel{border-top:1px solid var(--border);padding:12px 16px;}
  .articles-panel .liste{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;}
  .articles-panel label{display:inline-flex;align-items:center;gap:5px;font-size:12.5px;
    border:1px solid var(--border);border-radius:999px;padding:5px 10px;cursor:pointer;}
  .articles-panel label.on{background:var(--blue-bg);border-color:var(--blue);color:var(--blue);font-weight:600;}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>Console Usine</h1>
      <p class="sub">Flux des lignes (façon n8n) + pupitre de simulation — chaque action envoie un événement réel, comme un automate.</p>
    </div>
    <div style="display:flex;gap:8px;align-items:center;">
      <span id="wsPill" class="pill off"><span class="dot"></span> WebSocket…</span>
      <button id="autoBtn" onclick="toggleAuto()">Simulation auto : …</button>
    </div>
  </header>

  <div class="card scenarios" style="margin-bottom:16px;">
    <button class="scenario" onclick="lancerScenario('panne-critique')">
      ⚡ Panne critique <small>Surchauffe broche sur une machine en production : l'OF est bloqué.</small>
    </button>
    <button class="scenario" onclick="lancerScenario('derive-qualite')">
      🧪 Dérive qualité <small>Rafale de rebuts (mauvais réglage) : le taux de rebut explose.</small>
    </button>
    <button class="scenario" onclick="lancerScenario('rupture-stock')">
      📦 Rupture de stock <small>Le stock d'une matière première passe sous le seuil d'alerte.</small>
    </button>
  </div>

  <div class="card" style="margin-bottom:16px;padding:0;overflow:hidden;">
    <div class="flux-bar">
      <h3>Flux des lignes <small class="muted" style="font-weight:400;">— nœuds = lignes, flèches = la sortie alimente l'entrée · cliquez un nœud pour ses articles, une flèche pour la supprimer</small></h3>
      <label class="field">Source
        <select id="lienSource" style="min-width:140px;"></select>
      </label>
      <label class="field">→ alimente
        <select id="lienTarget" style="min-width:140px;"></select>
      </label>
      <button class="primary" onclick="creerLien()">Lier</button>
    </div>
    <div class="flux-wrap"><svg id="fluxSvg"></svg></div>
    <div class="articles-panel" id="articlesPanel" style="display:none;">
      <div style="font-size:12.5px;font-weight:700;" id="articlesTitre"></div>
      <div class="liste" id="articlesListe"></div>
    </div>
  </div>

  <div class="grid cols-2">
    <div class="grid" style="align-content:start;">
      <div class="card row">
        <label class="field">Machine
          <select id="machineSelect" onchange="onMachineChange()"></select>
        </label>
        <label class="field">Ordre de fabrication
          <select id="ordreSelect"><option value="">— aucun —</option></select>
        </label>
      </div>

      <div class="card" id="stateCard">
        <div class="empty">Choisissez une machine ci-dessus.</div>
      </div>

      <div class="card">
        <h3>Commandes</h3>
        <div class="btn-grid">
          <button class="big primary" onclick="action('start')">▶ Démarrer</button>
          <button class="big" onclick="action('pause')">⏸ Pause</button>
          <button class="big danger" onclick="action('stop')">■ Arrêter</button>
          <button class="big" onclick="action('alarme')">⚠ Alarme</button>
        </div>

        <details open>
          <summary>Réglages avancés</summary>
          <div class="section-title">Temps de cycle</div>
          <div class="row">
            <label class="field">Secondes / unité
              <input type="number" id="cycleTime" value="4" min="0.1" step="0.1" style="width:110px;" />
            </label>
            <button onclick="applyCycleTime()">Appliquer</button>
          </div>

          <div class="section-title">Production manuelle</div>
          <div class="row">
            <label class="field">Bonnes unités
              <input type="number" id="qtyBonne" value="1" min="1" style="width:90px;" />
            </label>
            <button onclick="produireBonne()">Générer</button>
          </div>
          <div class="row" style="margin-top:8px;">
            <label class="field">Rebuts
              <input type="number" id="qtyRebut" value="1" min="1" style="width:90px;" />
            </label>
            <label class="field">Cause
              <select id="causeRebut"></select>
            </label>
            <button class="danger" onclick="produireRebut()">Générer rebut</button>
          </div>

          <div class="section-title">Arrêt</div>
          <div class="row">
            <label class="field">Cause d'arrêt
              <select id="causeArret"></select>
            </label>
            <button onclick="declencherArret()">Déclencher l'arrêt</button>
            <button onclick="resoudreArret()">Résoudre l'arrêt</button>
          </div>

          <div class="section-title">Maintenance</div>
          <div class="row">
            <button onclick="demarrerMaintenance()">🔧 Démarrer maintenance</button>
            <button onclick="terminerMaintenance()">Terminer maintenance</button>
          </div>

          <div class="section-title">Tags capteurs</div>
          <div class="row">
            <label class="field">Tag
              <input type="text" id="tagName" value="Temperature_C" style="width:150px;" />
            </label>
            <label class="field">Valeur
              <input type="text" id="tagValue" style="width:100px;" />
            </label>
            <button onclick="envoyerTag()">Envoyer</button>
          </div>
          <div id="tagList" class="tag-list"></div>
        </details>
      </div>
    </div>

    <div class="grid" style="align-content:start;">
      <div class="card" style="padding:0;overflow:hidden;">
        <div style="padding:14px 16px;border-bottom:1px solid var(--border);">
          <h3 style="margin:0;">Flux d'événements</h3>
        </div>
        <div class="log" id="eventLog"><div class="empty">Aucun événement pour le moment.</div></div>
      </div>
    </div>
  </div>
</div>

<div id="toast" class="toast"></div>

<script>
const API = "%%API_PREFIX%%";
const API_KEY = "%%API_KEY%%";
const CAUSES_ARRET = ["PANNE_MECANIQUE","PANNE_ELECTRIQUE","ATTENTE_MATIERE","CHANGEMENT_SERIE","REGLAGE_MACHINE","MANQUE_OPERATEUR","NETTOYAGE","MAINTENANCE_PLANIFIEE","MICRO_ARRET","QUALITE_BLOQUANTE","AUTRE"];
const CAUSES_REBUT = ["DEFAUT_MATIERE","DEFAUT_DIMENSIONNEL","DEFAUT_VISUEL","MAUVAIS_REGLAGE","ERREUR_OPERATEUR","PROBLEME_MACHINE","NON_CONFORMITE_PROCESS","AUTRE"];
const EVENT_LABEL = {
  MACHINE_STARTED:"Machine démarrée", MACHINE_STOPPED:"Machine arrêtée", MACHINE_IDLE:"Machine en pause",
  MACHINE_ALARM:"Alarme machine", MACHINE_MAINTENANCE:"Passage en maintenance",
  PRODUCTION_COUNT_UPDATED:"Compteur production mis à jour", GOOD_UNIT_PRODUCED:"Unité bonne produite",
  SCRAP_UNIT_PRODUCED:"Unité rejetée produite", CYCLE_TIME_CHANGED:"Temps de cycle modifié",
  DOWNTIME_STARTED:"Arrêt déclenché", DOWNTIME_RESOLVED:"Arrêt résolu", QUALITY_EVENT_CREATED:"Événement qualité",
  MAINTENANCE_STARTED:"Maintenance démarrée", MAINTENANCE_ENDED:"Maintenance terminée", SENSOR_TAG_UPDATED:"Tag capteur mis à jour",
};
const STATUT_INFO = {
  MARCHE:{ic:"🟢",label:"En marche"}, ARRET:{ic:"⚪",label:"À l'arrêt"}, PAUSE:{ic:"🟡",label:"En pause"},
  PANNE:{ic:"🔴",label:"En panne"}, MAINTENANCE:{ic:"🔧",label:"Maintenance"},
};

let machines = [];
let machineId = null;
let machine = null;

function fillSelect(id, values, fmt){
  const el = document.getElementById(id);
  el.innerHTML = "";
  values.forEach(v => {
    const opt = document.createElement("option");
    opt.value = v; opt.textContent = fmt ? fmt(v) : v.replaceAll("_"," ").toLowerCase();
    el.appendChild(opt);
  });
}
fillSelect("causeArret", CAUSES_ARRET);
fillSelect("causeRebut", CAUSES_REBUT);

function toast(msg, isError){
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 3200);
}

async function api(path, opts){
  const headers = Object.assign({"Content-Type":"application/json"}, opts && opts.headers);
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  const res = await fetch(API + path, Object.assign({}, opts, {headers}));
  if (!res.ok){
    let detail = res.statusText;
    try { const j = await res.json(); detail = j.message || j.detail || detail; } catch(e){}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

async function loadMachines(){
  machines = await api("/machines");
  const sel = document.getElementById("machineSelect");
  sel.innerHTML = "";
  if (machines.length === 0){
    sel.innerHTML = '<option value="">Aucune machine</option>';
    return;
  }
  machines.forEach(m => {
    const opt = document.createElement("option");
    opt.value = m.id; opt.textContent = m.code + " — " + m.nom;
    sel.appendChild(opt);
  });
  machineId = machines[0].id;
  sel.value = machineId;
}

async function loadOrdres(){
  const ordres = await api("/ordres-fabrication");
  const dispo = ordres.filter(o => o.statut === "PLANIFIE" || o.statut === "EN_COURS");
  const sel = document.getElementById("ordreSelect");
  sel.innerHTML = '<option value="">— aucun —</option>';
  dispo.forEach(o => {
    const opt = document.createElement("option");
    opt.value = o.id; opt.textContent = o.numero + " · " + o.code_article + " (" + o.quantite_planifiee + " " + o.unite + ")";
    sel.appendChild(opt);
  });
}

function onMachineChange(){
  machineId = Number(document.getElementById("machineSelect").value) || null;
  refreshMachine();
  refreshEvents();
}

async function refreshMachine(){
  if (machineId == null){ machine = null; renderState(); return; }
  try {
    machine = await api("/machines/" + machineId);
    renderState();
  } catch(e){ toast(e.message, true); }
}

async function refreshEvents(){
  const log = document.getElementById("eventLog");
  if (machineId == null){ log.innerHTML = '<div class="empty">Aucun événement pour le moment.</div>'; return; }
  try {
    const events = await api("/machines/" + machineId + "/evenements?limit=50");
    if (events.length === 0){ log.innerHTML = '<div class="empty">Aucun événement pour le moment.</div>'; return; }
    log.innerHTML = events.map(e => {
      const payload = Object.entries(e.payload || {}).filter(([,v]) => v !== null && v !== undefined && v !== "")
        .map(([k,v]) => k + "=" + v).join(" · ");
      return '<div class="row-item"><div class="top"><span>' + (EVENT_LABEL[e.type] || e.type) +
        '</span><span class="time">' + new Date(e.created_at).toLocaleTimeString("fr-FR") + '</span></div>' +
        (payload ? '<div class="payload">' + payload + '</div>' : '') + '</div>';
    }).join("");

    const tags = events.filter(e => e.type === "SENSOR_TAG_UPDATED").slice(0,5);
    document.getElementById("tagList").innerHTML = tags.length === 0 ? "" :
      tags.map(e => (e.payload.tag) + " = " + (e.payload.valeur) + " (" + new Date(e.created_at).toLocaleTimeString("fr-FR") + ")").join("<br/>");
  } catch(e){ /* silencieux : la machine peut avoir disparu de la liste */ }
}

function renderState(){
  const card = document.getElementById("stateCard");
  if (!machine){ card.innerHTML = '<div class="empty">Choisissez une machine ci-dessus.</div>'; return; }
  const info = STATUT_INFO[machine.statut] || {ic:"⚙️", label:machine.statut};
  card.innerHTML =
    '<div class="state">' +
      '<div class="badge"><div class="ic">' + info.ic + '</div><div class="lbl">' + info.label + '</div><div class="sub">' + machine.nom + '</div></div>' +
      '<div class="stat"><div class="num">' + (machine.trs != null ? Math.round(machine.trs*100)+"%" : "—") + '</div><div class="lbl">TRS</div></div>' +
      '<div class="stat"><div class="num" style="color:var(--green)">' + machine.quantite_bonne + '</div><div class="lbl">Unités bonnes</div></div>' +
      '<div class="stat"><div class="num" style="color:var(--red)">' + machine.quantite_rejetee + '</div><div class="lbl">Unités rejetées</div></div>' +
    '</div>' +
    (machine.downtime_actif ?
      '<div class="downtime-banner">Arrêt en cours : ' + machine.downtime_actif.cause.replaceAll("_"," ").toLowerCase() +
      (machine.downtime_actif.operator_comment ? " — " + machine.downtime_actif.operator_comment : "") + '</div>' : '') +
    '<details style="margin-top:14px;"><summary>Détails techniques</summary>' +
      '<div class="row" style="margin-top:10px;">' +
        '<div><div class="lbl muted">OF actif</div><div>' + (machine.numero_of_actif || "—") + '</div></div>' +
        '<div><div class="lbl muted">Cycle</div><div>' + (machine.temps_cycle_actuel_s ?? machine.temps_cycle_cible_s ?? "—") + ' s</div></div>' +
        '<div><div class="lbl muted">Qualité (TQ)</div><div>' + (machine.tq != null ? Math.round(machine.tq*100)+"%" : "—") + '</div></div>' +
        '<div><div class="lbl muted">Disponibilité (DO)</div><div>' + (machine.do != null ? Math.round(machine.do*100)+"%" : "—") + '</div></div>' +
      '</div>' +
    '</details>';
}

async function run(promise){
  try {
    machine = await promise;
    renderState();
    await refreshEvents();
  } catch(e){ toast(e.message, true); }
}

function action(kind){
  if (machineId == null) return toast("Choisissez une machine.", true);
  if (kind === "start") return run(api("/simulateur/machines/" + machineId + "/start", {method:"POST", body: JSON.stringify({ordre_fabrication_id: document.getElementById("ordreSelect").value ? Number(document.getElementById("ordreSelect").value) : null})}));
  if (kind === "stop") return run(api("/simulateur/machines/" + machineId + "/stop", {method:"POST", body:"{}"}));
  if (kind === "pause") return run(api("/simulateur/machines/" + machineId + "/pause", {method:"POST", body:"{}"}));
  if (kind === "alarme") return run(api("/simulateur/machines/" + machineId + "/alarme", {method:"POST", body: JSON.stringify({message:"Alarme déclenchée manuellement"})}));
}

function applyCycleTime(){
  if (machineId == null) return toast("Choisissez une machine.", true);
  const s = Number(document.getElementById("cycleTime").value);
  if (!(s > 0)) return toast("Temps de cycle invalide.", true);
  run(api("/simulateur/machines/" + machineId + "/cycle-time", {method:"POST", body: JSON.stringify({temps_cycle_s:s})}));
}

function produireBonne(){
  if (machineId == null) return toast("Choisissez une machine.", true);
  const q = Number(document.getElementById("qtyBonne").value) || 1;
  run(api("/simulateur/machines/" + machineId + "/production/bonne", {method:"POST", body: JSON.stringify({quantite:q})}));
}

function produireRebut(){
  if (machineId == null) return toast("Choisissez une machine.", true);
  const q = Number(document.getElementById("qtyRebut").value) || 1;
  const cause = document.getElementById("causeRebut").value;
  run(api("/simulateur/machines/" + machineId + "/production/rebut", {method:"POST", body: JSON.stringify({quantite:q, cause})}));
}

function declencherArret(){
  if (machineId == null) return toast("Choisissez une machine.", true);
  const cause = document.getElementById("causeArret").value;
  run(api("/simulateur/machines/" + machineId + "/arret/declencher", {method:"POST", body: JSON.stringify({cause})}));
}

function resoudreArret(){
  if (machineId == null) return toast("Choisissez une machine.", true);
  run(api("/simulateur/machines/" + machineId + "/arret/resoudre", {method:"POST", body:"{}"}));
}

function demarrerMaintenance(){
  if (machineId == null) return toast("Choisissez une machine.", true);
  run(api("/simulateur/machines/" + machineId + "/maintenance/demarrer", {method:"POST", body: JSON.stringify({type:"PREVENTIVE", description:"Maintenance déclenchée depuis la console"})}));
}

function terminerMaintenance(){
  if (machineId == null) return toast("Choisissez une machine.", true);
  run(api("/simulateur/machines/" + machineId + "/maintenance/terminer", {method:"POST", body:"{}"}));
}

function envoyerTag(){
  if (machineId == null) return toast("Choisissez une machine.", true);
  const tag = document.getElementById("tagName").value.trim();
  const raw = document.getElementById("tagValue").value.trim();
  if (!tag || raw === "") return;
  const num = Number(raw);
  const valeur = Number.isNaN(num) ? raw : num;
  run(api("/simulateur/machines/" + machineId + "/tag", {method:"POST", body: JSON.stringify({tag, valeur})}));
  document.getElementById("tagValue").value = "";
}

async function lancerScenario(nom){
  try {
    const r = await api("/simulateur/scenarios/" + nom, {method:"POST", body:"{}"});
    toast(r.message);
    await refreshMachine();
    await refreshEvents();
  } catch(e){ toast(e.message, true); }
}

/* ------------------- Canvas de flux des lignes (façon n8n) ------------------- */
const NODE_W = 200, NODE_H = 104, GAP_X = 260, GAP_Y = 140, PAD = 40;
const STATUT_COULEUR = {MARCHE:"#059669", ARRET:"#9ca3af", PAUSE:"#d97706", PANNE:"#dc2626", MAINTENANCE:"#2563eb"};
let flux = null;            // {lignes, liens, articles}
let ligneSelectionnee = null;

function esc(s){ return String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

async function chargerFlux(){
  flux = await api("/lignes-production/flux");
  remplirLienSelects();
  renderFlux();
}

function remplirLienSelects(){
  ["lienSource","lienTarget"].forEach(id => {
    const sel = document.getElementById(id);
    sel.innerHTML = "";
    flux.lignes.forEach(l => {
      const opt = document.createElement("option");
      opt.value = l.id; opt.textContent = l.code;
      sel.appendChild(opt);
    });
  });
  if (flux.lignes.length > 1) document.getElementById("lienTarget").selectedIndex = 1;
}

/* Couches gauche→droite par plus long chemin depuis les sources (cycles tolérés). */
function calculerCouches(){
  const couche = {};
  const preds = {};
  flux.lignes.forEach(l => { preds[l.id] = []; });
  flux.liens.forEach(li => { if (preds[li.target_id]) preds[li.target_id].push(li.source_id); });
  function prof(id, pile){
    if (couche[id] != null) return couche[id];
    if (pile.has(id)) return 0; // cycle : on coupe
    pile.add(id);
    const p = preds[id] || [];
    couche[id] = p.length === 0 ? 0 : 1 + Math.max(...p.map(x => prof(x, pile)));
    pile.delete(id);
    return couche[id];
  }
  flux.lignes.forEach(l => prof(l.id, new Set()));
  return couche;
}

function machinesDeLigne(ligneId){
  return machines.filter(m => m.ligne_production_id === ligneId);
}

function renderFlux(){
  if (!flux) return;
  const svg = document.getElementById("fluxSvg");
  const couche = calculerCouches();
  const parCouche = {};
  flux.lignes.forEach(l => {
    const c = couche[l.id] || 0;
    (parCouche[c] = parCouche[c] || []).push(l);
  });
  const pos = {};
  Object.keys(parCouche).forEach(c => {
    parCouche[c].forEach((l, i) => {
      pos[l.id] = {x: PAD + c * GAP_X, y: PAD + i * GAP_Y};
    });
  });
  const nbCouches = Object.keys(parCouche).length || 1;
  const maxRangee = Math.max(1, ...Object.values(parCouche).map(a => a.length));
  const W = PAD * 2 + (nbCouches - 1) * GAP_X + NODE_W;
  const H = PAD * 2 + (maxRangee - 1) * GAP_Y + NODE_H;
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  svg.style.height = Math.min(H, 480) + "px";
  svg.style.minWidth = W + "px";

  const artiParId = {};
  (flux.articles || []).forEach(a => { artiParId[a.id] = a; });

  let out = "";
  // Liens d'abord (sous les nœuds) : bezier + zone de clic large pour supprimer.
  flux.liens.forEach(li => {
    const s = pos[li.source_id], t = pos[li.target_id];
    if (!s || !t) return;
    const x1 = s.x + NODE_W, y1 = s.y + NODE_H/2, x2 = t.x, y2 = t.y + NODE_H/2;
    const dx = Math.max(40, (x2 - x1) / 2);
    const d = "M" + x1 + " " + y1 + " C" + (x1+dx) + " " + y1 + ", " + (x2-dx) + " " + y2 + ", " + x2 + " " + y2;
    out += '<path class="flux-edge-hit" d="' + d + '" onclick="supprimerLien(' + li.id + ')"></path>';
    out += '<path class="flux-edge" d="' + d + '" onclick="supprimerLien(' + li.id + ')"></path>';
  });
  // Nœuds.
  flux.lignes.forEach(l => {
    const p = pos[l.id];
    const ms = machinesDeLigne(l.id);
    const avecTrs = ms.filter(m => m.trs != null);
    const trs = avecTrs.length ? avecTrs.reduce((s,m) => s + m.trs, 0) / avecTrs.length : null;
    const trsCouleur = trs == null ? "var(--muted)" : trs >= 0.7 ? "var(--green)" : trs >= 0.4 ? "var(--amber)" : "var(--red)";
    const arts = (l.article_ids || []).map(id => artiParId[id] ? artiParId[id].code : id).join(", ");
    let chips = "";
    ms.slice(0, 5).forEach((m, i) => {
      const cx = 16 + i * 36;
      chips += '<circle cx="' + cx + '" cy="82" r="5" fill="' + (STATUT_COULEUR[m.statut] || "#9ca3af") + '"></circle>' +
        '<text x="' + (cx + 8) + '" y="85" class="sous">' + esc(m.code) + '</text>';
    });
    out += '<g class="flux-node' + (ligneSelectionnee === l.id ? " selected" : "") + '" transform="translate(' + p.x + ',' + p.y + ')" onclick="choisirLigne(' + l.id + ')">' +
      '<rect class="body" width="' + NODE_W + '" height="' + NODE_H + '" rx="12"></rect>' +
      '<text x="14" y="24" class="titre">' + esc(l.code) + '</text>' +
      '<text x="' + (NODE_W - 14) + '" y="24" text-anchor="end" class="trs" fill="' + trsCouleur + '">' + (trs != null ? Math.round(trs*100) + "%" : "—") + '</text>' +
      '<text x="14" y="42" class="sous">' + esc(l.designation).slice(0, 30) + '</text>' +
      '<text x="14" y="62" class="sous">' + (arts ? "Articles : " + esc(arts).slice(0, 34) : "Aucun article lié — cliquez pour choisir") + '</text>' +
      chips +
      '</g>';
  });
  svg.innerHTML = out;
}

async function creerLien(){
  const source = Number(document.getElementById("lienSource").value);
  const target = Number(document.getElementById("lienTarget").value);
  if (!source || !target) return;
  if (source === target) return toast("Une ligne ne peut pas s'alimenter elle-même.", true);
  try {
    await api("/lignes-production/liens", {method:"POST", body: JSON.stringify({source_id: source, target_id: target})});
    await chargerFlux();
    toast("Lien créé : le flux alimente la ligne cible.");
  } catch(e){ toast(e.message, true); }
}

async function supprimerLien(id){
  if (!confirm("Supprimer ce lien de flux ?")) return;
  try {
    await api("/lignes-production/liens/" + id, {method:"DELETE"});
    await chargerFlux();
    toast("Lien supprimé.");
  } catch(e){ toast(e.message, true); }
}

function renderPanneauArticles(){
  const panel = document.getElementById("articlesPanel");
  if (ligneSelectionnee == null){ panel.style.display = "none"; return; }
  const ligne = flux.lignes.find(l => l.id === ligneSelectionnee);
  if (!ligne){ panel.style.display = "none"; return; }
  panel.style.display = "";
  document.getElementById("articlesTitre").textContent =
    "Articles produits par " + ligne.code + " — " + ligne.designation;
  const liste = document.getElementById("articlesListe");
  liste.innerHTML = "";
  (flux.articles || []).forEach(a => {
    const on = (ligne.article_ids || []).includes(a.id);
    const label = document.createElement("label");
    label.className = on ? "on" : "";
    label.textContent = a.code + " · " + a.designation;
    label.onclick = () => toggleArticle(ligne.id, a.id);
    liste.appendChild(label);
  });
}

function choisirLigne(id){
  ligneSelectionnee = (ligneSelectionnee === id) ? null : id;
  renderFlux();
  renderPanneauArticles();
  if (ligneSelectionnee == null) return;
  // Confort : sélectionne la première machine de la ligne dans le pupitre dessous.
  const ms = machinesDeLigne(id);
  if (ms.length > 0){
    machineId = ms[0].id;
    document.getElementById("machineSelect").value = machineId;
    refreshMachine(); refreshEvents();
  }
}

async function toggleArticle(ligneId, articleId){
  const ligne = flux.lignes.find(l => l.id === ligneId);
  const ids = new Set(ligne.article_ids || []);
  if (ids.has(articleId)) ids.delete(articleId); else ids.add(articleId);
  try {
    const maj = await api("/lignes-production/" + ligneId + "/articles",
      {method:"PUT", body: JSON.stringify({article_ids: [...ids]})});
    ligne.article_ids = maj.article_ids;
    renderFlux();
    renderPanneauArticles();
  } catch(e){ toast(e.message, true); }
}

let autoActif = false;
async function refreshAuto(){
  try {
    const r = await api("/simulateur/auto");
    autoActif = r.actif;
    renderAutoBtn();
  } catch(e){}
}
function renderAutoBtn(){
  const btn = document.getElementById("autoBtn");
  btn.textContent = autoActif ? "■ Simulation auto : active" : "▶ Démarrer la simulation auto";
  btn.className = autoActif ? "primary" : "";
}
async function toggleAuto(){
  try {
    const r = await api("/simulateur/auto/" + (autoActif ? "stop" : "start"), {method:"POST", body:"{}"});
    autoActif = r.actif;
    renderAutoBtn();
    toast(autoActif ? "Simulation autonome démarrée : les machines en marche produisent toutes seules." : "Simulation autonome arrêtée.");
  } catch(e){ toast(e.message, true); }
}

function connectWs(){
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(proto + "://" + location.host + "/ws/dashboard");
  const pill = document.getElementById("wsPill");
  ws.onopen = () => { pill.className = "pill on"; pill.innerHTML = '<span class="dot"></span> WebSocket connecté'; };
  ws.onclose = () => {
    pill.className = "pill off"; pill.innerHTML = '<span class="dot"></span> WebSocket déconnecté';
    setTimeout(connectWs, 2000);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === "machine_update" && msg.machine){
        // Met à jour la copie locale pour les pastilles du canvas de flux.
        const i = machines.findIndex(m => m.id === msg.machine.id);
        if (i >= 0) machines[i] = msg.machine;
        renderFlux();
        if (msg.machine.id === machineId){
          machine = msg.machine;
          renderState();
          refreshEvents();
        }
      }
    } catch(e){}
  };
}

(async function init(){
  try {
    await loadMachines();
    await loadOrdres();
    await refreshMachine();
    await refreshEvents();
    await refreshAuto();
    await chargerFlux();
  } catch(e){ toast(e.message, true); }
  connectWs();
  setInterval(refreshAuto, 10000);
})();
</script>
</body>
</html>
"""


@router.get("/simulateur", response_class=HTMLResponse, include_in_schema=False)
async def page_simulateur() -> HTMLResponse:
    """Pupitre de simulation servi par le backend — remplace le panneau React."""
    settings = get_settings()
    api_key = settings.api_keys[0] if settings.api_keys else ""
    html = _PAGE.replace("%%API_PREFIX%%", settings.api_prefix).replace("%%API_KEY%%", api_key)
    return HTMLResponse(html)
