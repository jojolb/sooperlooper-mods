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
  const updateMeter = (meter, value) => {
    const amount = Math.max(0, Math.min(1, Number(value) || 0));
    meter.setAttribute("aria-valuenow", String(amount));
    meter.firstElementChild.style.width = `${amount * 100}%`;
  };
  const safe = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
  let statusTimer = 0;
  let commandQueue = Promise.resolve();
  let controlQueue = Promise.resolve();
  const pendingGlobalValues = new Map();
  const filePicker = $("file-picker");
  const pickerState = { mode: "", index: -1, path: "", selectedPath: "", entries: [] };
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
    if (!response.ok) {
      const error = new Error(result.error || "Erreur de communication");
      error.status = response.status;
      throw error;
    }
    return result;
  }

  function sendCommand(data) {
    commandQueue = commandQueue
      .then(() => controlQueue)
      .then(() => post("/api/command", data))
      .catch((error) => showStatus(error.message));
    return commandQueue;
  }

  function globalControlValue(name, serverValue, serverPendingValue) {
    const pending = pendingGlobalValues.get(name);
    if (!pending) {
      return serverPendingValue === undefined ? serverValue : serverPendingValue;
    }
    if (Number(serverPendingValue) === pending.value) {
      pending.sent = true;
      return pending.value;
    }
    if ((pending.sent && serverPendingValue === undefined) || Date.now() >= pending.expires) {
      pendingGlobalValues.delete(name);
      return serverValue;
    }
    return pending.value;
  }

  async function setControl(input) {
    const scope = input.dataset.scope;
    const control = input.dataset.control;
    const value = input.type === "checkbox" ? (input.checked ? 1 : 0) : Number(input.value);
    if (scope === "global" && (control === "sync_source" || control === "quantize")) {
      pendingGlobalValues.set(control, { value, expires: Date.now() + 5000, sent: false });
    }
    const request = controlQueue.then(() => post("/api/set", {
      scope,
      index: Number(input.dataset.index),
      control,
      value
    }));
    controlQueue = request.catch(() => {});
    try {
      await request;
      const pending = pendingGlobalValues.get(control);
      if (pending && pending.value === value) pending.sent = true;
    } catch (error) {
      const pending = pendingGlobalValues.get(control);
      if (pending && pending.value === value) pendingGlobalValues.delete(control);
      showStatus(error.message);
    }
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
    const loops = state.loops || [];
    [["tempo", "tempo-value", 1], ["input_gain", "input-gain-value", 2], ["dry", "dry-value", 2], ["wet", "wet-value", 2]].forEach(([name, label, digits]) => {
      const value = globals[name];
      const input = $(name.replaceAll("_", "-"));
      input.disabled = !state.connected;
      if (value !== undefined && document.activeElement !== input) {
        input.value = value;
        $(label).textContent = formatNumber(value, digits);
      }
      if (name === "wet") updateMeter($("global-wet-meter"), input.value);
    });
    const quantize = $("quantize");
    const pendingGlobals = state.pending_globals || {};
    const quantizeValue = globalControlValue(
      "quantize", globals.quantize ?? loops[0]?.quantize, pendingGlobals.quantize
    );
    quantize.disabled = !state.connected || loops.length === 0;
    if (quantizeValue !== undefined && document.activeElement !== quantize) {
      quantize.value = String(Math.trunc(Number(quantizeValue)));
    }
    const syncSource = $("sync-source");
    syncSource.disabled = !state.connected;
    syncSource.querySelectorAll("option[value]").forEach((option) => {
      const sourceLoop = Number(option.value);
      if (sourceLoop > 0) option.disabled = sourceLoop > state.loop_count;
    });
    const syncSourceValue = globalControlValue(
      "sync_source", globals.sync_source, pendingGlobals.sync_source
    );
    if (syncSourceValue !== undefined && document.activeElement !== syncSource) {
      syncSource.value = String(Math.trunc(Number(syncSourceValue)));
    }

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
          </div>
          <div class="loop-mix">
            <label class="control"><span>Feedback <b>—</b></span><input type="range" min="0" max="1" step="0.01" data-scope="loop" data-control="feedback" data-index="${index}"></label>
            <label class="control"><span>Gain entrée <b>—</b></span><input type="range" min="0" max="1" step="0.01" data-scope="loop" data-control="input_gain" data-index="${index}"></label>
            <label class="control wet-loop-control"><span>Wet <b>—</b></span><input type="range" min="0" max="1" step="0.01" data-scope="loop" data-control="wet" data-index="${index}"><span class="wet-meter" role="progressbar" aria-label="Wet boucle ${index + 1}" aria-valuemin="0" aria-valuemax="1" aria-valuenow="0"><span></span></span></label>
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
      const recordButton = card.querySelector('.command[data-command="record"]');
      const waitingState = Number(loop.state) === 1 || Number(loop.state) === 3;
      const waitingRecord = Number(loop.waiting) > 0 && waitingState;
      stateElement.classList.toggle("waiting", waitingState);
      recordButton.classList.toggle("waiting", waitingRecord);
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
        if (input.dataset.control === "wet") {
          updateMeter(input.closest(".control").querySelector(".wet-meter"), input.value);
        }
      });
      card.querySelectorAll(".loop-setting").forEach((input) => {
        const value = loop[input.dataset.control];
        if (value !== undefined && document.activeElement !== input) {
          input.checked = Number(value) !== 0;
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

  function closeFilePicker() {
    filePicker.hidden = true;
  }

  function updateSaveSelection() {
    const name = $("save-filename").value.trim();
    const validName = name && name !== "." && name !== ".." &&
      !name.includes("/") && !name.includes("\\") && name.toLowerCase().endsWith(".wav");
    pickerState.selectedPath = validName
      ? (pickerState.path ? `${pickerState.path}/${name}` : name)
      : "";
    $("file-confirm").disabled = !pickerState.selectedPath;
  }

  async function browseFiles(path = "") {
    $("file-error").textContent = "";
    try {
      const response = await fetch(`/api/files?path=${encodeURIComponent(path)}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Impossible de lire ce dossier");
      pickerState.path = data.path;
      pickerState.entries = data.entries;

      const crumbs = [{ name: "Fichiers", path: "" }];
      let current = "";
      for (const part of data.path ? data.path.split("/") : []) {
        current = current ? `${current}/${part}` : part;
        crumbs.push({ name: part, path: current });
      }
      $("file-breadcrumbs").innerHTML = crumbs.map((crumb) =>
        `<button type="button" data-open-path="${safe(crumb.path)}">${safe(crumb.name)}</button>`
      ).join("");

      const rows = data.entries.map((entry) => {
        const directory = entry.type === "directory";
        return `<button type="button" class="file-entry ${directory ? "directory" : "audio"}"
          ${directory ? `data-open-path="${safe(entry.path)}"` : `data-file-path="${safe(entry.path)}"`}
          aria-selected="false">${safe(entry.name)}</button>`;
      }).join("");
      $("file-list").innerHTML = rows || '<p class="file-empty">Aucun dossier ou fichier WAV dans ce dossier.</p>';
      if (data.truncated) $("file-error").textContent = "Liste partielle : plus de 1000 éléments.";

      if (pickerState.mode === "save") updateSaveSelection();
      else {
        pickerState.selectedPath = "";
        $("file-confirm").disabled = true;
      }
    } catch (error) {
      $("file-list").innerHTML = '<p class="file-empty">Dossier inaccessible.</p>';
      $("file-error").textContent = error.message;
    }
  }

  function openFilePicker(mode, index) {
    pickerState.mode = mode;
    pickerState.index = Number(index);
    pickerState.path = "";
    pickerState.selectedPath = "";
    pickerState.entries = [];
    $("file-mode").textContent = mode === "load" ? "CHARGER UNE BOUCLE" : "SAUVEGARDER UNE BOUCLE";
    $("file-title").textContent = mode === "load" ? "Choisir un fichier WAV" : "Enregistrer un fichier WAV";
    $("save-name-row").hidden = mode !== "save";
    $("save-filename").value = "";
    $("file-confirm").textContent = mode === "load" ? "Charger" : "Sauvegarder";
    $("file-confirm").disabled = true;
    filePicker.hidden = false;
    browseFiles("");
  }

  async function confirmFileSelection() {
    const filename = pickerState.selectedPath;
    if (!filename) return;
    try {
      if (pickerState.mode === "load") {
        await post("/api/load", { index: pickerState.index, filename });
        showStatus("Commande de chargement envoyée");
      } else {
        let overwrite = pickerState.entries.some((entry) =>
          entry.type === "file" && entry.path === filename
        );
        if (overwrite && !confirm(`Le fichier ${filename} existe déjà. Le remplacer ?`)) return;
        try {
          await post("/api/save", { index: pickerState.index, filename, overwrite });
        } catch (error) {
          if (error.status !== 409 || overwrite ||
              !confirm(`Le fichier ${filename} existe déjà. Le remplacer ?`)) throw error;
          overwrite = true;
          await post("/api/save", { index: pickerState.index, filename, overwrite });
        }
        showStatus("Commande de sauvegarde envoyée");
      }
      closeFilePicker();
    } catch (error) {
      $("file-error").textContent = error.message;
    }
  }

  $("file-list").addEventListener("click", (event) => {
    const directory = event.target.closest("[data-open-path]");
    if (directory) {
      browseFiles(directory.dataset.openPath);
      return;
    }
    const file = event.target.closest("[data-file-path]");
    if (!file) return;
    if (pickerState.mode === "load") {
      pickerState.selectedPath = file.dataset.filePath;
      $("file-list").querySelectorAll("[data-file-path]").forEach((item) => {
        item.setAttribute("aria-selected", String(item === file));
      });
      $("file-confirm").disabled = false;
    } else {
      $("save-filename").value = file.textContent;
      updateSaveSelection();
    }
  });
  $("file-breadcrumbs").addEventListener("click", (event) => {
    const crumb = event.target.closest("[data-open-path]");
    if (crumb) browseFiles(crumb.dataset.openPath);
  });
  $("save-filename").addEventListener("input", updateSaveSelection);
  $("file-confirm").addEventListener("click", confirmFileSelection);
  $("file-cancel").addEventListener("click", closeFilePicker);
  $("file-close").addEventListener("click", closeFilePicker);
  filePicker.addEventListener("click", (event) => {
    if (event.target === filePicker) closeFilePicker();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !filePicker.hidden) closeFilePicker();
  });

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
      openFilePicker("load", button.dataset.index);
      return;
    }
    if (button.dataset.command === "save") {
      openFilePicker("save", button.dataset.index);
      return;
    }
    if (event.detail !== 0) return;
    sendCommand({ index: Number(button.dataset.index), command: button.dataset.command, phase: "hit" });
  });
  document.addEventListener("input", (event) => {
    if (event.target.matches('input[type="range"]')) {
      const label = event.target.closest(".control").querySelector("b");
      if (label) label.textContent = formatNumber(event.target.value, event.target.dataset.control === "tempo" ? 1 : 2);
      if (event.target.dataset.control === "wet") {
        updateMeter(event.target.closest(".control").querySelector(".wet-meter"), event.target.value);
      }
    }
  });
  document.addEventListener("change", (event) => {
    if (event.target.matches('input[type="range"]')) changeValue(event.target);
    if (event.target.matches("#sync-source, .loop-setting")) {
      setControl(event.target);
    }
    if (event.target.matches("#quantize")) setControl(event.target);
  });
  $("add-mono").addEventListener("click", () => post("/api/loops", { action: "add", channels: 1 }).catch((e) => showStatus(e.message)));
  $("add-stereo").addEventListener("click", () => post("/api/loops", { action: "add", channels: 2 }).catch((e) => showStatus(e.message)));
  $("remove-loop").addEventListener("click", () => {
    if (confirm("Supprimer la dernière boucle ?")) post("/api/loops", { action: "remove" }).catch((e) => showStatus(e.message));
  });
  refresh();
  setInterval(refresh, 250);
})();
