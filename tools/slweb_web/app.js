(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const loopsEl = $("loops");
  const formatTime = (seconds) => {
    const value = Number(seconds);
    if (!Number.isFinite(value) || value < 0) return "0:00";
    const mins = Math.floor(value / 60);
    return `${mins}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
  };
  const formatNumber = (value, digits = 1) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "—";
  const safe = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
  let statusTimer = 0;
  let commandQueue = Promise.resolve();
  const showStatus = (message) => {
    $("status-message").textContent = message || "";
    clearTimeout(statusTimer);
    if (message) statusTimer = setTimeout(() => { $("status-message").textContent = ""; }, 3500);
  };

  async function post(url, data) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Erreur de communication");
    return result;
  }

  function sendCommand(data) {
    commandQueue = commandQueue.then(() => post("/api/command", data)).catch((error) => showStatus(error.message));
    return commandQueue;
  }

  function render(state) {
    $("connection-dot").classList.toggle("online", state.connected);
    $("connection-label").textContent = state.connected ? "Moteur connecté" : "Moteur hors ligne";
    $("engine-address").textContent = `${state.engine_host}:${state.engine_port}`;
    $("loop-count").textContent = `${Math.min(state.loop_count, 4)} / 4 boucles`;
    $("empty-state").hidden = state.loop_count > 0;
    $("empty-state").textContent = state.connected ? "Aucune boucle. Ajoutez une boucle mono ou stéréo." : "En attente du moteur SooperLooper…";
    $("hidden-loops").hidden = !state.hidden_loops;
    $("hidden-loops").textContent = state.hidden_loops ? `${state.hidden_loops} boucle(s) supplémentaire(s) masquée(s) — slweb affiche au maximum quatre boucles.` : "";
    $("add-mono").disabled = $("add-stereo").disabled = !state.connected || state.loop_count >= 4;
    $("remove-loop").disabled = !state.connected || state.loop_count <= 0;

    const globals = state.globals || {};
    [["tempo", "tempo-value", 1], ["input_gain", "input-gain-value", 2], ["dry", "dry-value", 2], ["wet", "wet-value", 2]].forEach(([name, label, digits]) => {
      const value = globals[name];
      const input = $(name.replaceAll("_", "-"));
      input.disabled = !state.connected;
      if (value !== undefined && document.activeElement !== input) {
        input.value = value;
        $(label).textContent = formatNumber(value, digits);
      }
    });
    const syncSource = $("sync-source");
    syncSource.disabled = !state.connected;
    syncSource.querySelectorAll("option[value]").forEach((option) => {
      const sourceLoop = Number(option.value);
      if (sourceLoop > 0) option.disabled = sourceLoop > state.loop_count;
    });
    if (globals.sync_source !== undefined && document.activeElement !== syncSource) {
      syncSource.value = String(Math.trunc(Number(globals.sync_source)));
    }

    const loops = state.loops || [];
    const currentIndexes = [...loopsEl.children].map((card) => Number(card.dataset.loopIndex));
    const nextIndexes = loops.map((loop) => loop.index);
    if (currentIndexes.join(",") !== nextIndexes.join(",")) {
      loopsEl.innerHTML = loops.map((loop) => {
        const index = loop.index;
        return `<article class="loop-card" data-loop-index="${index}" aria-label="Boucle ${index + 1}">
          <div class="loop-head"><div class="loop-title"><span class="loop-number">LOOP ${String(index + 1).padStart(2, "0")}</span><span class="state"></span></div>
            <span class="time"></span></div>
          <div class="position-track"><div class="position-fill"></div></div>
          <div class="commands">
            <button class="command record" data-command="record" data-index="${index}">RECORD</button>
            <button class="command" data-command="overdub" data-index="${index}">OVERDUB</button>
            <button class="command" data-command="mute" data-index="${index}">MUTE</button>
            <button class="command" data-command="undo" data-index="${index}">UNDO</button>
            <button class="command" data-command="redo" data-index="${index}">REDO</button>
            <button class="command" data-command="reverse" data-index="${index}">REVERSE</button>
            <button class="command" data-command="oneshot" data-index="${index}">ONCE</button>
            <button class="command" data-command="trigger" data-index="${index}">TRIGGER</button>
            <button class="command" data-command="solo" data-index="${index}">SOLO</button>
            <button class="command load-button" data-command="load" data-index="${index}">LOAD…</button>
            <button class="command load-button" data-command="save" data-index="${index}">SAVE…</button>
          </div>
          <div class="loop-options">
            <label class="toggle"><input type="checkbox" class="loop-setting" data-scope="loop" data-control="sync" data-index="${index}"><span>Sync</span></label>
            <label class="toggle"><input type="checkbox" class="loop-setting" data-scope="loop" data-control="playback_sync" data-index="${index}"><span>Play sync</span></label>
            <label class="control quantize-control"><span>Quantize</span><select class="loop-setting" data-scope="loop" data-control="quantize" data-index="${index}">
              <option value="3">Loop</option>
              <option value="2">8th</option>
              <option value="0">Off</option>
              <option value="1">Cycle</option>
            </select></label>
          </div>
          <div class="loop-mix">
            <label class="control"><span>Feedback <b>—</b></span><input type="range" min="0" max="1" step="0.01" data-scope="loop" data-control="feedback" data-index="${index}"></label>
            <label class="control"><span>Gain entrée <b>—</b></span><input type="range" min="0" max="1" step="0.01" data-scope="loop" data-control="input_gain" data-index="${index}"></label>
          </div>
        </article>`;
      }).join("");
    }
    loops.forEach((loop) => {
      const card = loopsEl.querySelector(`[data-loop-index="${loop.index}"]`);
      const stateElement = card.querySelector(".state");
      const stateClass = (loop.state_label || "").toLowerCase().replace(/[^a-z0-9-]/g, "-");
      stateElement.className = `state ${stateClass}`;
      stateElement.textContent = loop.state_label || "inconnu";
      const pos = Number(loop.loop_pos) || 0;
      const len = Number(loop.loop_len) || 0;
      card.querySelector(".time").textContent = `${formatTime(pos)} / ${formatTime(len)}`;
      card.querySelector(".position-fill").style.width = `${len > 0 ? Math.max(0, Math.min(100, (pos / len) * 100)) : 0}%`;
      card.querySelectorAll(".command").forEach((button) => { button.disabled = !state.connected; });
      card.querySelectorAll('input[type="range"]').forEach((input) => {
        const value = loop[input.dataset.control];
        const editing = document.activeElement === input;
        if (value !== undefined && !editing) input.value = value;
        input.disabled = !state.connected;
        if (value !== undefined && !editing) {
          input.closest(".control").querySelector("b").textContent = formatNumber(value, 2);
        }
      });
      card.querySelectorAll(".loop-setting").forEach((input) => {
        const value = loop[input.dataset.control];
        if (value !== undefined && document.activeElement !== input) {
          if (input.type === "checkbox") input.checked = Number(value) !== 0;
          else input.value = String(Math.trunc(Number(value)));
        }
        input.disabled = !state.connected;
      });
    });
    if (state.error) showStatus(state.error);
  }

  async function refresh() {
    try {
      const response = await fetch("/api/state", { cache: "no-store" });
      if (!response.ok) throw new Error("Le serveur web ne répond pas");
      render(await response.json());
    } catch (error) {
      $("connection-dot").classList.remove("online");
      $("connection-label").textContent = "Serveur inaccessible";
      showStatus(error.message);
    }
  }

  async function changeValue(input) {
    const value = Number(input.value);
    const label = input.closest(".control").querySelector("b");
    if (label) label.textContent = formatNumber(value, input.dataset.control === "tempo" ? 1 : 2);
    try {
      await post("/api/set", {
        scope: input.dataset.scope,
        index: Number(input.dataset.index),
        control: input.dataset.control,
        value
      });
    } catch (error) {
      showStatus(error.message);
    }
  }

  async function loadLoop(button) {
    const filename = prompt("Chemin du fichier WAV accessible à la machine du moteur SooperLooper :");
    if (!filename) return;
    try {
      await post("/api/load", { index: Number(button.dataset.index), filename });
      showStatus("Commande de chargement envoyée");
    } catch (error) {
      showStatus(error.message);
    }
  }

  async function saveLoop(button) {
    const filename = prompt("Chemin de destination du fichier WAV sur la machine du moteur SooperLooper :");
    if (!filename) return;
    try {
      await post("/api/save", { index: Number(button.dataset.index), filename });
      showStatus("Commande de sauvegarde envoyée");
    } catch (error) {
      showStatus(error.message);
    }
  }

  document.addEventListener("pointerdown", async (event) => {
    const button = event.target.closest(".command");
    if (!button || event.button !== 0) return;
    if (button.dataset.command === "load" || button.dataset.command === "save") return;
    event.preventDefault();
    button.classList.add("pressed");
    button.setPointerCapture?.(event.pointerId);
    await sendCommand({ index: Number(button.dataset.index), command: button.dataset.command, phase: "down" });
  });
  const release = async (event) => {
    const button = event.target.closest?.(".command");
    if (!button || !button.classList.contains("pressed")) return;
    button.classList.remove("pressed");
    await sendCommand({ index: Number(button.dataset.index), command: button.dataset.command, phase: "up" });
  };
  document.addEventListener("pointerup", release);
  document.addEventListener("pointercancel", release);
  document.addEventListener("lostpointercapture", release);
  document.addEventListener("click", (event) => {
    const button = event.target.closest(".command");
    if (!button) return;
    if (button.dataset.command === "load") {
      loadLoop(button);
      return;
    }
    if (button.dataset.command === "save") {
      saveLoop(button);
      return;
    }
    if (event.detail !== 0) return;
    sendCommand({ index: Number(button.dataset.index), command: button.dataset.command, phase: "hit" });
  });
  document.addEventListener("input", (event) => {
    if (event.target.matches('input[type="range"]')) {
      const label = event.target.closest(".control").querySelector("b");
      if (label) label.textContent = formatNumber(event.target.value, event.target.dataset.control === "tempo" ? 1 : 2);
    }
  });
  document.addEventListener("change", (event) => {
    if (event.target.matches('input[type="range"]')) changeValue(event.target);
    if (event.target.matches("#sync-source, .loop-setting")) {
      post("/api/set", {
        scope: event.target.dataset.scope,
        index: Number(event.target.dataset.index),
        control: event.target.dataset.control,
        value: event.target.type === "checkbox" ? (event.target.checked ? 1 : 0) : Number(event.target.value)
      }).catch((error) => showStatus(error.message));
    }
  });
  $("add-mono").addEventListener("click", () => post("/api/loops", { action: "add", channels: 1 }).catch((e) => showStatus(e.message)));
  $("add-stereo").addEventListener("click", () => post("/api/loops", { action: "add", channels: 2 }).catch((e) => showStatus(e.message)));
  $("remove-loop").addEventListener("click", () => {
    if (confirm("Supprimer la dernière boucle ?")) post("/api/loops", { action: "remove" }).catch((e) => showStatus(e.message));
  });
  refresh();
  setInterval(refresh, 250);
})();
