"""Console Simulateur : page HTML autonome (aucune dépendance frontend).

Sert un pupitre de pilotage complet du simulateur machine — mêmes actions que
l'ancien panneau React (`/simulateur`), mais rendu côté serveur en HTML/JS
vanilla. La page consomme directement l'API JSON existante
(`/api/v1/simulateur/...`, `/api/v1/machines`, `/api/v1/ordres-fabrication`)
et écoute `/ws/dashboard` pour les mises à jour temps réel — aucun nouvel
endpoint métier n'était nécessaire.

Mise en page : console 100vh sans défilement de page (chaque panneau défile en
interne), pensée pour être comprise en démo par un public non technique —
bandeau « 1. Lancer la production / 2. Provoquer un incident / 3. Nova réagit ».
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
<title>Nova — Console usine</title>
<style>
  :root{
    --bg:#eef0f3; --panel:#ffffff; --border:#d7dce2; --text:#1b2430; --muted:#68727e;
    --accent:#1f5eff; --accent-fg:#ffffff;
    --green:#0e7a52; --green-bg:#0e7a5214; --amber:#b45f06; --amber-bg:#b45f0614;
    --red:#c22a2a; --red-bg:#c22a2a12; --blue:#1f5eff; --blue-bg:#1f5eff12;
  }
  *{box-sizing:border-box;}
  html,body{height:100%;}
  body{
    margin:0;background:var(--bg);color:var(--text);
    font:13.5px/1.45 "Segoe UI",-apple-system,system-ui,sans-serif;
    display:flex;flex-direction:column;overflow:hidden;
  }
  .mono{font-family:ui-monospace,Consolas,monospace;}

  /* ---------- Bandeau haut ---------- */
  header{
    flex:none;height:50px;background:#141b24;color:#e9edf2;
    display:flex;align-items:center;gap:14px;padding:0 16px;
  }
  header .titre{font-size:14.5px;font-weight:700;letter-spacing:.02em;white-space:nowrap;}
  header .titre span{color:#8fa2b8;font-weight:500;}
  header .sous{color:#8fa2b8;font-size:12px;flex:1;min-width:0;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap;}
  .pill{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;
    font-size:11.5px;font-weight:600;white-space:nowrap;}
  .pill .dot{width:7px;height:7px;border-radius:999px;background:currentColor;}
  .pill.on{background:#12351f;color:#4ade80;}
  .pill.off{background:#3a1d1d;color:#f28b8b;}

  /* ---------- Corps 100vh ---------- */
  main{
    flex:1;min-height:0;display:grid;gap:10px;padding:10px;
    grid-template-columns:minmax(0,1.7fr) minmax(300px,.8fr);
  }
  .colonne{display:flex;flex-direction:column;gap:10px;min-height:0;min-width:0;}
  .panel{
    background:var(--panel);border:1px solid var(--border);border-radius:8px;
    display:flex;flex-direction:column;min-height:0;overflow:hidden;
  }
  .panel-titre{
    flex:none;display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;
    padding:9px 14px;border-bottom:1px solid var(--border);
  }
  .panel-titre b{font-size:12px;text-transform:uppercase;letter-spacing:.06em;}
  .panel-titre small{color:var(--muted);font-size:11.5px;}

  /* ---------- Bandeau démo : 3 étapes ---------- */
  .demo{flex:none;display:flex;align-items:stretch;gap:10px;padding:10px 14px;flex-wrap:wrap;}
  .etape{display:flex;align-items:center;gap:10px;min-width:0;}
  .etape .num{
    flex:none;width:22px;height:22px;border-radius:999px;background:#141b24;color:#fff;
    font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center;
  }
  .fleche{align-self:center;color:var(--muted);font-size:16px;}
  button{
    cursor:pointer;border-radius:6px;border:1px solid var(--border);background:#fff;color:var(--text);
    font-size:13px;font-weight:600;padding:0 12px;height:32px;display:inline-flex;
    align-items:center;gap:6px;font-family:inherit;
  }
  button:hover:not(:disabled){background:#f3f5f8;}
  button:disabled{opacity:.45;cursor:not-allowed;}
  button.primary{background:var(--accent);color:var(--accent-fg);border-color:var(--accent);}
  button.primary:hover:not(:disabled){background:#1b52dd;}
  button.danger{background:var(--red-bg);color:var(--red);border-color:transparent;}
  button.auto{height:40px;font-size:13.5px;background:var(--accent);color:#fff;border-color:var(--accent);}
  button.auto:hover:not(:disabled){background:#1b52dd;}
  button.auto.on{background:var(--green-bg);color:var(--green);border-color:var(--green);}
  button.auto.on:hover:not(:disabled){background:#0e7a5222;}
  .incident{
    height:40px;text-align:left;font-weight:600;border-left:3px solid var(--border);
    display:flex;flex-direction:column;justify-content:center;gap:0;line-height:1.25;padding:0 12px;
  }
  .incident small{font-weight:400;color:var(--muted);font-size:11px;}
  .incident.panne{border-left-color:var(--red);}
  .incident.qualite{border-left-color:var(--amber);}
  .incident.stock{border-left-color:var(--blue);}
  .etape .texte{font-size:12px;color:var(--muted);line-height:1.35;max-width:230px;}
  .etape .texte b{color:var(--text);}

  /* ---------- Plan de l'usine (flux) ---------- */
  .flux-panel{flex:1.1;min-height:150px;}
  .flux-wrap{flex:1;min-height:0;overflow:auto;position:relative;}
  #fluxSvg{display:block;min-width:100%;}
  .flux-node{cursor:pointer;}
  .flux-node rect.body{fill:#fff;stroke:var(--border);stroke-width:1.5;transition:stroke .15s;}
  .flux-node.selected rect.body{stroke:var(--accent);stroke-width:2.5;}
  .flux-node text{font-family:inherit;}
  .flux-node .titre{font-size:13px;font-weight:700;fill:var(--text);}
  .flux-node .sous{font-size:10.5px;fill:var(--muted);}
  .flux-node .trs{font-size:11px;font-weight:700;}
  .flux-edge{fill:none;stroke:var(--accent);stroke-width:2;stroke-dasharray:7 6;
    animation:flux-dash .7s linear infinite;cursor:pointer;}
  .flux-edge:hover{stroke:var(--red);stroke-width:3;}
  .flux-edge-hit{fill:none;stroke:transparent;stroke-width:14;cursor:pointer;}
  @keyframes flux-dash{to{stroke-dashoffset:-13;}}
  .lier-bar{display:flex;gap:8px;align-items:flex-end;margin-left:auto;}
  .articles-panel{flex:none;border-top:1px solid var(--border);padding:10px 14px;max-height:110px;overflow:auto;}
  .articles-panel .liste{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;}
  .articles-panel label{display:inline-flex;align-items:center;gap:5px;font-size:12px;
    border:1px solid var(--border);border-radius:999px;padding:4px 10px;cursor:pointer;}
  .articles-panel label.on{background:var(--blue-bg);border-color:var(--accent);color:var(--accent);font-weight:600;}

  /* ---------- Pupitre machine ---------- */
  .pupitre{flex:1.3;}
  .pupitre-corps{flex:1;min-height:0;overflow:auto;padding:12px 14px;}
  .row{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end;}
  label.field{display:flex;flex-direction:column;gap:3px;font-size:11px;color:var(--muted);font-weight:600;}
  select,input[type=text],input[type=number]{
    height:32px;border:1px solid var(--border);border-radius:6px;padding:0 8px;
    font-size:13px;background:#fff;color:var(--text);font-family:inherit;
  }
  select{min-width:180px;}
  .etat{
    display:flex;align-items:center;gap:20px;flex-wrap:wrap;
    border:1px solid var(--border);border-radius:6px;padding:10px 14px;margin-top:10px;background:#fafbfc;
  }
  .etat .statut{display:flex;align-items:center;gap:8px;font-weight:700;font-size:14px;min-width:150px;}
  .etat .statut .dot{width:11px;height:11px;border-radius:999px;flex:none;}
  .etat .statut small{display:block;font-weight:400;color:var(--muted);font-size:11px;}
  .stat{text-align:center;}
  .stat .num{font-size:19px;font-weight:800;}
  .stat .lbl{font-size:10.5px;color:var(--muted);}
  .downtime-banner{margin-top:8px;border-radius:6px;border:1px solid var(--red);
    background:var(--red-bg);color:var(--red);padding:7px 12px;font-size:12px;}
  .btn-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:12px;}
  .btn-grid button{height:42px;font-size:13.5px;justify-content:center;}
  .section-title{font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;
    color:var(--muted);font-weight:700;margin:14px 0 6px;}
  details{border-top:1px solid var(--border);margin-top:14px;padding-top:8px;}
  summary{cursor:pointer;font-size:12px;font-weight:600;color:var(--muted);}
  .tag-list{margin-top:8px;font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--muted);}
  .empty{padding:26px 12px;text-align:center;color:var(--muted);font-size:12.5px;}

  /* ---------- Journal ---------- */
  .log{flex:1;min-height:0;overflow-y:auto;}
  .log .row-item{padding:7px 14px;border-bottom:1px solid var(--border);font-size:12px;}
  .log .row-item:last-child{border-bottom:none;}
  .log .row-item .top{display:flex;justify-content:space-between;gap:8px;font-weight:600;}
  .log .row-item .time{color:var(--muted);font-weight:400;flex:none;}
  .log .row-item .payload{margin-top:1px;color:var(--muted);font-family:ui-monospace,Consolas,monospace;font-size:10.5px;}

  .toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:#141b24;color:#fff;
    padding:10px 18px;border-radius:8px;font-size:13px;box-shadow:0 6px 20px rgba(0,0,0,.22);opacity:0;
    transition:opacity .2s;pointer-events:none;z-index:50;max-width:560px;text-align:center;}
  .toast.show{opacity:1;}
  .toast.error{background:var(--red);}

  /* Petits écrans : empilement simple avec défilement de page (plus de 100vh strict). */
  @media(max-width:980px){
    body{overflow:auto;height:auto;}
    main{display:flex;flex-direction:column;flex:none;height:auto;min-height:0;}
    .colonne{min-height:auto;}
    .panel{min-height:0;flex:none;}
    .flux-wrap{max-height:300px;}
    .pupitre-corps{overflow:visible;}
    .log{max-height:320px;}
    header .sous{display:none;}
  }
</style>
</head>
<body>

<header>
  <div class="titre">NOVA <span>· Console usine</span></div>
  <div class="sous">Simulateur d'atelier : chaque bouton envoie un vrai événement machine au système, comme le ferait un automate.</div>
  <span id="wsPill" class="pill off"><span class="dot"></span> Temps réel…</span>
</header>

<main>
  <div class="colonne">

    <!-- Étapes de démo : 1 lancer, 2 provoquer, 3 Nova réagit -->
    <div class="panel">
      <div class="demo">
        <div class="etape">
          <div class="num">1</div>
          <button id="autoBtn" class="auto" onclick="toggleAuto()">…</button>
        </div>
        <div class="fleche">→</div>
        <div class="etape">
          <div class="num">2</div>
          <button class="incident panne" onclick="lancerScenario('panne-critique')">
            Provoquer une panne <small>une machine en production s'arrête net</small>
          </button>
          <button class="incident qualite" onclick="lancerScenario('derive-qualite')">
            Provoquer des défauts <small>trop de pièces rejetées d'un coup</small>
          </button>
          <button class="incident stock" onclick="lancerScenario('rupture-stock')">
            Vider un stock <small>une matière première passe sous le seuil</small>
          </button>
        </div>
        <div class="fleche">→</div>
        <div class="etape">
          <div class="num">3</div>
          <div class="texte"><b>Nova détecte l'incident en quelques secondes</b> et propose une action sur le tableau de bord (et WhatsApp).</div>
        </div>
      </div>
    </div>

    <!-- Plan de l'usine -->
    <div class="panel flux-panel">
      <div class="panel-titre">
        <b>Plan de l'usine</b>
        <small>chaque bloc est une ligne de production · les flèches montrent quelle ligne alimente laquelle · cliquez un bloc pour le piloter</small>
        <div class="lier-bar">
          <label class="field">Source
            <select id="lienSource" style="min-width:110px;"></select>
          </label>
          <label class="field">alimente
            <select id="lienTarget" style="min-width:110px;"></select>
          </label>
          <button onclick="creerLien()">Lier</button>
        </div>
      </div>
      <div class="flux-wrap"><svg id="fluxSvg"></svg></div>
      <div class="articles-panel" id="articlesPanel" style="display:none;">
        <div style="font-size:12px;font-weight:700;" id="articlesTitre"></div>
        <div class="liste" id="articlesListe"></div>
      </div>
    </div>

    <!-- Pupitre machine -->
    <div class="panel pupitre">
      <div class="panel-titre">
        <b>Piloter une machine</b>
        <small>démarrer / arrêter une machine précise, ou provoquer des événements à la main</small>
      </div>
      <div class="pupitre-corps">
        <div class="row">
          <label class="field">Machine
            <select id="machineSelect" onchange="onMachineChange()"></select>
          </label>
          <label class="field">Ordre de fabrication à produire
            <select id="ordreSelect"><option value="">— aucun —</option></select>
          </label>
        </div>

        <div id="stateCard"><div class="empty">Choisissez une machine ci-dessus.</div></div>

        <div class="btn-grid">
          <button class="primary" onclick="action('start')">▶ Démarrer</button>
          <button onclick="action('pause')">⏸ Pause</button>
          <button class="danger" onclick="action('stop')">■ Arrêter</button>
          <button onclick="action('alarme')">⚠ Alarme</button>
        </div>

        <details>
          <summary>Réglages avancés (production manuelle, arrêts, maintenance, capteurs)</summary>

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
            <button onclick="demarrerMaintenance()">Démarrer maintenance</button>
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
  </div>

  <!-- Journal temps réel -->
  <div class="colonne">
    <div class="panel" style="flex:1;">
      <div class="panel-titre">
        <b>Journal des événements</b>
        <small>tout ce qui se passe sur la machine sélectionnée, en direct</small>
      </div>
      <div class="log" id="eventLog"><div class="empty">Aucun événement pour le moment.</div></div>
    </div>
  </div>
</main>

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
  MARCHE:{couleur:"#0e7a52",label:"En marche"}, ARRET:{couleur:"#9aa3ad",label:"À l'arrêt"},
  PAUSE:{couleur:"#b45f06",label:"En pause"}, PANNE:{couleur:"#c22a2a",label:"En panne"},
  MAINTENANCE:{couleur:"#1f5eff",label:"Maintenance"},
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
  const info = STATUT_INFO[machine.statut] || {couleur:"#9aa3ad", label:machine.statut};
  card.innerHTML =
    '<div class="etat">' +
      '<div class="statut"><span class="dot" style="background:' + info.couleur + '"></span>' +
        '<span>' + info.label + '<small>' + machine.nom + '</small></span></div>' +
      '<div class="stat"><div class="num">' + (machine.trs != null ? Math.round(machine.trs*100)+"%" : "—") + '</div><div class="lbl">TRS</div></div>' +
      '<div class="stat"><div class="num" style="color:var(--green)">' + machine.quantite_bonne + '</div><div class="lbl">Unités bonnes</div></div>' +
      '<div class="stat"><div class="num" style="color:var(--red)">' + machine.quantite_rejetee + '</div><div class="lbl">Unités rejetées</div></div>' +
      '<div class="stat"><div class="num">' + (machine.temps_cycle_actuel_s ?? machine.temps_cycle_cible_s ?? "—") + ' s</div><div class="lbl">Cycle / unité</div></div>' +
      '<div class="stat"><div class="num mono" style="font-size:14px;">' + (machine.numero_of_actif || "—") + '</div><div class="lbl">OF en cours</div></div>' +
    '</div>' +
    (machine.downtime_actif ?
      '<div class="downtime-banner">Arrêt en cours : ' + machine.downtime_actif.cause.replaceAll("_"," ").toLowerCase() +
      (machine.downtime_actif.operator_comment ? " — " + machine.downtime_actif.operator_comment : "") + '</div>' : '');
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

/* ------------------- Plan de l'usine (flux des lignes) ------------------- */
const NODE_W = 200, NODE_H = 104, GAP_X = 260, GAP_Y = 140, PAD = 40;
const STATUT_COULEUR = {MARCHE:"#0e7a52", ARRET:"#9aa3ad", PAUSE:"#b45f06", PANNE:"#c22a2a", MAINTENANCE:"#1f5eff"};
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
      chips += '<circle cx="' + cx + '" cy="82" r="5" fill="' + (STATUT_COULEUR[m.statut] || "#9aa3ad") + '"></circle>' +
        '<text x="' + (cx + 8) + '" y="85" class="sous">' + esc(m.code) + '</text>';
    });
    out += '<g class="flux-node' + (ligneSelectionnee === l.id ? " selected" : "") + '" transform="translate(' + p.x + ',' + p.y + ')" onclick="choisirLigne(' + l.id + ')">' +
      '<rect class="body" width="' + NODE_W + '" height="' + NODE_H + '" rx="10"></rect>' +
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
  btn.textContent = autoActif ? "■ Production en cours — arrêter" : "▶ Lancer la production";
  btn.className = "auto" + (autoActif ? " on" : "");
}
async function toggleAuto(){
  try {
    const r = await api("/simulateur/auto/" + (autoActif ? "stop" : "start"), {method:"POST", body:"{}"});
    autoActif = r.actif;
    renderAutoBtn();
    toast(autoActif ? "Production lancée : les machines en marche produisent toutes seules." : "Production automatique arrêtée.");
  } catch(e){ toast(e.message, true); }
}

function connectWs(){
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(proto + "://" + location.host + "/ws/dashboard");
  const pill = document.getElementById("wsPill");
  ws.onopen = () => { pill.className = "pill on"; pill.innerHTML = '<span class="dot"></span> Temps réel connecté'; };
  ws.onclose = () => {
    pill.className = "pill off"; pill.innerHTML = '<span class="dot"></span> Temps réel déconnecté';
    setTimeout(connectWs, 2000);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === "machine_update" && msg.machine){
        // Met à jour la copie locale pour les pastilles du plan de l'usine.
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
