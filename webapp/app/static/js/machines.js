/* ----------------------------
   NO_DATA helpers
   - If NO_DATA => state "unknown" (no critical/normal)
-----------------------------*/
function isNoDataCard(card) {
  if (!card) return false;
  // Prefer dataset if you add it later (ex: data-no-data="1")
  if (card.dataset && card.dataset.noData != null) return card.dataset.noData === "1";
  // Fallback: in current template, the "NO DATA" flag is rendered as a pill:
  // <span class="pill no-data">NO DATA</span>
  // not as a class on the card itself.
  return !!card.querySelector(".pill.no-data");
}

function applyUnknownState(card, isUnknown) {
  if (!card) return;
  card.classList.toggle("no-data", !!isUnknown);
  if (isUnknown) card.classList.remove("critical", "normal");
}

/* ----------------------------
   Helpers: value parsing (fallback)
-----------------------------*/
function parseBooleanFromDisplay(val) {
  const s = String(val ?? "").trim().toLowerCase();
  if (s === "1" || s === "true" || s === "actif" || s === "on" || s === "yes") return true;
  if (s === "0" || s === "false" || s === "inactif" || s === "off" || s === "no") return false;
  return !!s;
}

function enforceNumericOrDash(input) {
  const v = input.value.trim();
  if (v === "-") return;
  input.value = input.value.replace(/[^\d.,-]/g, "");
}


/* ----------------------------
   Filters (cookies) - P4
-----------------------------*/
function restoreFiltersFromCookie(machineId) {
  console.log("📦 Tentative de restauration des filtres pour:", machineId);
  
  const cookieKey = `filters_${machineId}`;
  const raw = document.cookie.split(";").find((c) => c.trim().startsWith(cookieKey + "="));
  
  const hideEl = document.getElementById("hideInactive");
  const searchEl = document.getElementById("metricSearch");
  const advEl = document.getElementById("showAdvanced");
  
  // Double vérification que les éléments existent
  if (!searchEl || !hideEl) {
    console.error("❌ Éléments de filtres non trouvés dans restoreFiltersFromCookie");
    // Réessayer après un court délai
    setTimeout(() => {
      if (document.getElementById("metricSearch") && document.getElementById("hideInactive")) {
        console.log("🔄 Réessai de restauration des filtres...");
        restoreFiltersFromCookie(machineId);
      }
    }, 50);
    return;
  }
  
  // Valeurs par défaut - IMPORTANT : "Avancées" désactivé par défaut
  let hideInactive = false;
  let showAdvanced = false;
  let searchValue = "";
  
  if (raw) {
    try {
      const value = decodeURIComponent(raw.split("=")[1]);
      const filters = JSON.parse(value);
      
      hideInactive = !!filters.hideInactive;
      showAdvanced = filters.showAdvanced != null ? !!filters.showAdvanced : false;
      searchValue = filters.search || "";
      console.log("✅ Filtres restaurés depuis cookie:", { hideInactive, showAdvanced, searchValue });
    } catch (e) {
      console.warn("❌ Erreur restauration des filtres :", e);
    }
  } else {
    console.log("📭 Pas de cookie de filtres trouvé, utilisation des valeurs par défaut");
  }
  
  // Appliquer les valeurs
  hideEl.checked = hideInactive;
  searchEl.value = searchValue;
  if (advEl) {
    advEl.checked = showAdvanced;
    advEl.dataset.initialized = "true";
  }
  
  // Appliquer le filtrage avec les nouvelles valeurs
  if (typeof window.filterMetrics === "function") {
    // Petit délai pour s'assurer que les valeurs sont bien appliquées
    setTimeout(() => {
      window.filterMetrics();
    }, 20);
  }
}


function saveFiltersToCookie() {
  const searchEl = document.getElementById("metricSearch");
  const machineId = searchEl?.dataset?.machineId;
  if (!machineId) return;

  const hideInactive = !!document.getElementById("hideInactive")?.checked;
  const showAdvanced = !!document.getElementById("showAdvanced")?.checked;
  const search = searchEl.value || ""

  const filters = { hideInactive, showAdvanced, search };
  document.cookie = `filters_${machineId}=${encodeURIComponent(JSON.stringify(filters))}; path=/; max-age=31536000`;
}


function applyThresholdToCardUI(card, th) {
  if (!card) return;

  // Keep search text in sync (filter)
  const name = (card.querySelector(".metric-label")?.textContent || "").trim();
  const display = (card.querySelector(".metric-last-value")?.textContent || "").trim();
  if (name) card.dataset.search = `${name} ${display}`.trim();

  // Store last threshold in dataset (optional debug)
  card.dataset.thCond = th?.condition ?? "";
  card.dataset.thNum = th?.value_num == null ? "" : String(th.value_num);
  card.dataset.thBool = th?.value_bool == null ? "" : ((th.value_bool === true || th.value_bool === 1 || th.value_bool === "1") ? "1" : "0");
  card.dataset.thStr = th?.value_str == null ? "" : String(th.value_str);
  card.dataset.thSeverity = th?.severity == null ? "" : String(th.severity);
}


/* ----------------------------
   Autosave (alerting / pause) - P2/P6
-----------------------------*/
/* ----------------------------
   Autosave (alerting / pause / threshold) - P2/P6
   
   Gère la sauvegarde automatique des formulaires de métriques :
   - alerting : toggle activation des alertes
   - pause : toggle pause de la métrique
   - threshold : création/modification de seuils avec transformation JSON
   
   Flow :
   1. Validation du formulaire et contexte
   2. Transformation du payload selon l'endpoint
   3. Envoi fetch avec gestion des erreurs
   4. Mise à jour de l'UI en cas de succès
-----------------------------*/
function autoSave(form) {
  const card = form?.closest?.(".metric-card, .service-card");
  if (!card) {
    console.warn("❌ Impossible de trouver la carte parent du formulaire.");
    return Promise.resolve(null);
  }

  const endpoint = String(form.dataset.endpoint || "").toLowerCase();
  const formData = new FormData(form);
  
  // ✅ DEBUG : Voir tous les champs du formulaire
  console.log("📋 Form endpoint:", endpoint);
  console.log("📋 FormData entries:");
  for (let [key, value] of formData.entries()) {
    console.log(`  ${key} = ${value}`);
  }

  // ------------------------------------------------------------
  // 2. Patch "thresholdExists" pour endpoint threshold
  // Le template HTML utilise data-threshold-exists (kebab-case)
  // mais JS lit form.dataset.thresholdExists (camelCase)
  // On synchronise les deux
  // ------------------------------------------------------------
  if (endpoint === "threshold") {
    const fromAttr = form.getAttribute("data-threshold-exists");
    if (fromAttr != null && form.dataset.thresholdExists == null) {
      form.dataset.thresholdExists = String(fromAttr);
    }
  }

  // ------------------------------------------------------------
  // 3. NO_DATA => état unknown (pas de critical/normal)
  // ------------------------------------------------------------
  if (isNoDataCard(card)) {
    applyUnknownState(card, true);
  } else {
    applyUnknownState(card, false);
  }

  // ------------------------------------------------------------
  // 4. Calcul des flags (alertEnabled, paused, isInactive)
  // ------------------------------------------------------------
  const flags = computeCardFlags(card, form, formData);
  const isInactive = !!flags.isInactive;

  card.classList.toggle("inactive", isInactive);

  // ------------------------------------------------------------
  // 5. Enable/disable fields (alerting uniquement)
  // Quand alerting est OFF, on désactive les champs de seuil
  // ------------------------------------------------------------
  if (endpoint === "alerting") {
    const alertEnabled = !!flags.alertEnabled;
    updateThresholdBadge(card);
    form
      .querySelectorAll(
        'select[name="comparison"], input[name="value_num"], select[name="value_bool"], input[name="value_str"], select[name="severity"]'
      )
      .forEach((el) => {
        el.disabled = !alertEnabled;
      });
  }

  // ------------------------------------------------------------
  // 6. Validation seuil (threshold uniquement)
  // - Pas d'autosave si aucun seuil n'existe (sauf forceSave)
  // - Refuse valeurs invalides
  // ------------------------------------------------------------
  if (endpoint === "threshold") {
    try {
      updateThresholdUI(form);
    } catch (_) {}

    // Validation : refuse valeurs vides ou invalides
    if (typeof validateThresholdForm === "function" && !validateThresholdForm(form)) {
      return Promise.resolve(null);
    }

    const exists = (form.dataset.thresholdExists || "0") === "1";
    const forceSave = (form.dataset.forceSave || "0") === "1";

    // Aucun seuil existant + pas forcé => on ne fait rien (pas de création auto)
    if (!exists && !forceSave) {
      return Promise.resolve(null);
    }

    // Si forcé (bouton "Définir"), on consomme le flag ici (évite double-save)
    if (forceSave) {
      form.dataset.forceSave = "0";
    }
  }

  // ------------------------------------------------------------
  // 7. Recompute critical state (si nécessaire)
  // Ne pas recalculer quand on clique juste le toggle alerting/pause
  // ------------------------------------------------------------
  const trigger = document.activeElement;
  const isAlertToggle = endpoint === "alerting" && trigger?.name === "alert_enabled";
  const isPauseToggle = endpoint === "pause" && trigger?.name === "paused";
  const shouldRecompute = !isAlertToggle && !isPauseToggle;

  if (shouldRecompute) {
    if (isInactive) {
      card.classList.remove("critical", "normal");
    } else if (isNoDataCard(card)) {
      applyUnknownState(card, true);
    } else {
      const thresholdForm = card.querySelector('form[data-endpoint="threshold"]');
      if (thresholdForm) updateCriticalState(thresholdForm);
    }
  }

  // ------------------------------------------------------------
  // 8. Animation 💾 (indicateur global de sauvegarde)
  // ------------------------------------------------------------
  const globalIndicator = document.getElementById("global-saving-indicator");
  if (globalIndicator) {
    globalIndicator.style.opacity = "1";
    setTimeout(() => (globalIndicator.style.opacity = "0"), 1000);
  }

  // ------------------------------------------------------------
  // 9. Construction du body selon l'endpoint
  // - alerting/pause => URLSearchParams (urlencoded)
  // - threshold => JSON avec mapping des champs
  // - autres => FormData classique
  // ------------------------------------------------------------
  const fetchOpts = {
    method: "POST",
    credentials: "include",
    redirect: "manual",
    headers: { "X-Requested-With": "fetch" },
    body: null,
  };

  if (endpoint === "alerting") {
    // Toggle alerting : envoie alert_enabled=0 ou 1
    const cb = form.querySelector('input[name="alert_enabled"]');
    const enabled = !!cb?.checked;

    const params = new URLSearchParams();
    params.set("alert_enabled", enabled ? "1" : "0");

    fetchOpts.headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8";
    fetchOpts.body = params.toString();
    
  } else if (endpoint === "pause") {
    // Toggle pause : envoie paused=0 ou 1
    const cb = form.querySelector('input[name="paused"]');
    const paused = !!cb?.checked;

    const params = new URLSearchParams();
    params.set("paused", paused ? "1" : "0");

    fetchOpts.headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8";
    fetchOpts.body = params.toString();
      
  } else if (endpoint === "threshold") {
    const body = {};

    if (formData.has("comparison")) {
      body.comparison = formData.get("comparison");
    }
    if (formData.has("severity")) {
      body.severity = formData.get("severity");
    }

    if (formData.has("value_num")) {
      const val = formData.get("value_num");
      body.threshold = parseFloat(val.replace(",", "."));
    } else if (formData.has("value_bool")) {
      body.value_bool = formData.get("value_bool") === "1";
    } else if (formData.has("value_str")) {
      body.value_str = formData.get("value_str");
    }

    // ✅ DEBUG : Voir ce qui est vraiment envoyé
    console.log("📤 Threshold body avant stringify:", body);
    console.log("📤 Threshold body JSON:", JSON.stringify(body));

    fetchOpts.headers["Content-Type"] = "application/json";
    fetchOpts.body = JSON.stringify(body);

  } else {
    // Autres endpoints : FormData classique (multipart)
    fetchOpts.body = formData;
    // ⚠️ NE PAS fixer Content-Type pour FormData (boundary auto)
  }

  // ------------------------------------------------------------
  // 10. Fetch + gestion des réponses
  // ------------------------------------------------------------
  return fetch(form.action, fetchOpts)
    .then(async (response) => {
      // Auth guard webapp : redirection si non authentifié
      if (response.status === 303) {
        window.location.href = response.headers.get("location") || "/login";
        return response;
      }
      if (response.status === 401 || response.headers.get("X-Auth-Redirect") === "1") {
        window.location.href = "/login";
        return response;
      }

      // Tentative de parsing JSON de la réponse
      let payload = null;
      try {
        payload = await response.json();
      } catch (_) {
        payload = null;
      }

      // Erreur HTTP (4xx, 5xx)
      if (!response.ok) {
        console.error("❌ Erreur lors de la sauvegarde :", response.status, payload || response.statusText);
        return response;
      }

      // ------------------------------------------------------------
      // 11. Succès threshold : mise à jour UI
      // ------------------------------------------------------------
      if (endpoint === "threshold" && payload && payload.success) {
        const th = payload.threshold || null;

        // Mettre à jour les datasets de la card (pour debug/filtres)
        applyThresholdToCardUI(card, th);

        // ✅ Le seuil existe maintenant → autosave autorisé pour les prochaines modifs
        form.dataset.thresholdExists = "1";

        // ✅ Supprimer le bouton "Définir" (désormais inutile)
        const btn = form.querySelector(".define-btn");
        if (btn) btn.remove();

        // Mettre à jour le badge de seuil (retire "À définir")
        updateThresholdBadge(card);

        // Optionnel : remettre à jour l'UI (bordure/validation)
        try {
          updateThresholdUI(form);
        } catch (_) {}
      }

      // Refresh du filtrage (si les flags inactive/active ont changé)
      try {
        filterMetrics();
      } catch (_) {}

      return response;
    })
    .catch((err) => {
      console.error("❌ Erreur de requête :", err);
      return null;
    });
}


/* ----------------------------
   Search helpers (data-search)
-----------------------------*/
function getSearchText(card, fallbackSelector = "") {
  // Prefer dedicated attribute produced by template (machine_detail_inner.html / machines.html)
  const ds = (card?.dataset?.search || "").trim();
  if (ds) return ds.toLowerCase();

  // Fallback: label text
  if (fallbackSelector) {
    const t = (card.querySelector(fallbackSelector)?.textContent || "").trim();
    if (t) return t.toLowerCase();
  }

  // Last resort: entire card text
  return (card.textContent || "").trim().toLowerCase();
}




/* ----------------------------
Filtering (metrics + services)
-----------------------------*/

/*
Type	          État	        Premier chargement	  Avec "Avancées"	    Avec "Actifs"	    "Avancées"+"Actifs"
Suggérée	      Active	      ✅ Afficher	        ✅ Afficher	       ✅ Afficher	      ✅ Afficher
Suggérée	      Inactive	    ✅ Afficher	        ✅ Afficher	       ✅ Afficher	      ✅ Afficher
Non-suggérée	  Active	      ✅ Afficher	        ✅ Afficher	       ✅ Afficher	      ✅ Afficher
Non-suggérée	  Inactive	    ❌ Masquer	          ✅ Afficher	       ❌ Masquer	      ❌ Masquer
*/

function filterMetrics() {
  console.log("🪄 Filtrage des métriques");
  
  const searchEl = document.getElementById("metricSearch");
  const hideEl = document.getElementById("hideInactive");  // "Actifs"
  const advEl = document.getElementById("showAdvanced");   // "Avancées"
  if (!searchEl || !hideEl) return;

  const metricsGrid = document.getElementById("metricsGrid");
  // on marquera data-ready=1 à la fin, après le 1er filtrage

  const search = (searchEl.value || "").toLowerCase().trim();
  const hideInactive = !!hideEl.checked;    // Masquer les inactives
  const showAdvanced = !!advEl?.checked;    // Afficher les avancées
  
  console.log("Filtres actuels:", { search, hideInactive, showAdvanced });
  
  let metricCount = 0;
  let serviceCount = 0;
  
  // ========================================
  // MÉTRIQUES
  // ========================================
  document.querySelectorAll(".metric-card").forEach((card) => {
    const haystack = getSearchText(card, ".metric-label");
    const matchesSearch = haystack.includes(search);
    
    // ✅ Recalcule l'état inactive depuis les toggles
    let isInactive = card.classList.contains("inactive");
    try {
      const alertForm = card.querySelector('form[data-endpoint="alerting"]');
      const pauseForm = card.querySelector('form[data-endpoint="pause"]');
      
      const alertEnabled = !!alertForm?.querySelector('input[name="alert_enabled"]')?.checked;
      const paused = !!pauseForm?.querySelector('input[name="paused"]')?.checked;
      
      isInactive = (!alertEnabled) || paused;
      card.classList.toggle("inactive", isInactive);
    } catch (_) {
      // Fallback : garde la classe existante
    }
    
    const isSuggested = (card.dataset.suggested || "0") === "1";
    
    // ========================================
    // RÈGLES DE FILTRAGE - VERSION CORRIGÉE
    // ========================================
    
    // ✅ RÈGLE 1 : Filtre par recherche (prioritaire)
    if (!matchesSearch) {
      card.style.display = "none";
      return;
    }
    
    // ✅ DÉCISION D'AFFICHAGE
    let shouldShow = false;
    
    // D'abord, on détermine ce qui devrait être affiché SANS le filtre "Actifs"
    
    // 1. Les métriques suggérées (actives ou inactives) → affichées par défaut
    if (isSuggested) {
      shouldShow = true;
    }
    // 2. Les métriques non-suggérées actives → affichées par défaut
    else if (!isInactive) {
      shouldShow = true;
    }
    // 3. Les métriques non-suggérées inactives → seulement si "Avancées" coché
    else if (showAdvanced) {
      shouldShow = true;
    }
    
    // ✅ APPLICATION DU FILTRE "ACTIFS" (S'APPLIQUE À TOUT)
    // Si "Actifs" est coché → on masque TOUTES les métriques inactives
    // (y compris les suggérées inactives, comme indiqué dans votre tableau)
    if (hideInactive && isInactive) {
      shouldShow = false;
    }
    
    card.style.display = shouldShow ? "" : "none";
    if (shouldShow) metricCount++;
  });
  
  // ========================================
  // EMPTY-STATE PAR GROUPE
  // ========================================
  document.querySelectorAll(".metric-group").forEach((group) => {
    const hasVisible = Array.from(group.querySelectorAll(".metric-card"))
      .some((c) => c.style.display !== "none");
    
    const empty = group.querySelector(".group-no-results");
    const grid = group.querySelector(".grid");
    if (empty) empty.style.display = hasVisible ? "none" : "block";
    if (grid) grid.style.display = hasVisible ? "" : "none";
  });

  // ✅ Une fois le filtrage appliqué, on révèle l'ensemble
  if (metricsGrid) metricsGrid.dataset.ready = "1";
  
  // ========================================
  // SERVICES (inchangé)
  // ========================================
  document.querySelectorAll(".service-card").forEach((card) => {
    const haystack = getSearchText(card, ".service-label");
    const isInactive = card.classList.contains("inactive");
    const matchesSearch = haystack.includes(search);
    
    const show = matchesSearch && (!hideInactive || !isInactive);
    card.style.display = show ? "" : "none";
    if (show) serviceCount++;
  });
  
  // ========================================
  // NO RESULTS
  // ========================================
  const noM = document.getElementById("noMetricsFound");
  const noS = document.getElementById("noServicesFound");
  if (noM) noM.style.display = metricCount === 0 ? "block" : "none";
  if (noS) noS.style.display = serviceCount === 0 ? "block" : "none";
}


function updateThresholdUI(form) {
  /**
   * Met à jour l'état UI de l'éditeur de seuil :
   * - ajoute/retire la classe "is-missing" sur le champ valeur
   * - active/désactive le bouton "Définir" si présent
   * - optionnel: disable save button si présent
   */
  const card = form.closest(".metric-card, .service-card");
  if (!card) return;

  // Cherche le champ valeur
  const valEl =
    form.querySelector('[name="value_num"]') ||
    form.querySelector('[name="value_str"]') ||
    form.querySelector('[name="value_bool"]');

  if (!valEl) return;

  const valid = validateThresholdForm(form);
  valEl.classList.toggle("is-missing", !valid);

  // Bouton "Définir" (ajouté dans la vue)
  const defineBtn = form.querySelector(".define-btn");
  if (defineBtn) {
    // Si NO_DATA ou pas de last_value, tu peux aussi le désactiver côté template (déjà fait)
    defineBtn.disabled = !valid;
  }

  // Si tu as un bouton submit/save (fallback)
  const saveBtn = form.querySelector('button[type="submit"], .btn-save');
  if (saveBtn) saveBtn.disabled = !valid;
}

async function applySuggestedThreshold(form) {
  /**
   * Clique "Définir" :
   * - valide la valeur selon le type
   * - force la sauvegarde même si thresholdExists=0
   * - si succès :
   *    - supprime le bouton Définir
   *    - supprime uniquement la pill "Non défini" (sans toucher à "Alerte OFF")
   *    - bascule en autosave (thresholdExists=1)
   */
  if (!form) return;

  // UI: marque champs invalides + disable/enable bouton Définir
  try { updateThresholdUI(form); } catch (_) {}

  // Validation typée
  if (typeof validateThresholdForm === "function" && !validateThresholdForm(form)) {
    console.warn("⛔ Valeur de seuil invalide, création refusée.");
    return;
  }

  // Force un save même si aucun seuil n'existe encore
  form.dataset.forceSave = "1";

  try {
    // Debug utile si besoin
    // console.log("🧷 Définir: forceSave=1, payload=", Object.fromEntries(new FormData(form).entries()));

    const r = await window.autoSave(form); // autoSave doit return fetch(...)
    if (!r || !r.ok) return;

    // Succès => le seuil existe maintenant
    form.dataset.thresholdExists = "1";

    // Retire le bouton Définir
    const btn = form.querySelector(".define-btn");
    if (btn) btn.remove();

    // ✅ IMPORTANT : la pill "Non défini" n'est souvent PAS dans le form.
    // On remonte à la card et on supprime UNIQUEMENT la pill qui contient "Non défini".
    const card = form.closest(".metric-card, .service-card");
    // if (card) {
    //   card.querySelectorAll(".pill.state-off").forEach((pill) => {
    //     const txt = (pill.textContent || "").trim().toLowerCase();
    //     if (txt.includes("non défini") || txt.includes("non defini")) {
    //       pill.remove();
    //     }
    //   });
    // }

    // Visuel : retire la classe "is-missing" si tout est OK
    try { 
        updateThresholdUI(form); 
        if (card) updateThresholdBadge(card);
    } catch (_) {}
  } catch (e) {
    console.error("❌ Définir seuil : erreur", e);
  }
}

function validateThresholdForm(form) {
  /**
   * Valide un formulaire de seuil (endpoint="threshold") en fonction :
   * - du type de donnée (data-raw-type sur la carte)
   * - de la présence d'une valeur (pas vide / pas "-")
   * - de la compatibilité type/valeur (numérique parsable, bool 0/1, string non vide)
   *
   * IMPORTANT :
   * - On ne bloque pas les autres forms (alerting/pause).
   * - Ici, on parle uniquement de l'éditeur de seuil.
   */
  const card = form.closest(".metric-card, .service-card");
  const rawType = (card?.dataset?.rawType || "none").toLowerCase();

  // Cherche l'élément de valeur présent dans ce form (selon type)
  const numEl = form.querySelector('input[name="value_num"]');
  const boolEl = form.querySelector('select[name="value_bool"]');
  const strEl = form.querySelector('input[name="value_str"]');

  // Si aucun champ de valeur => rien à valider
  const valEl = numEl || boolEl || strEl;
  if (!valEl) return true;

  // Valeur brute saisie
  const v = String(valEl.value ?? "").trim();

  // Ancien placeholder "-" : on le considère invalide (UX => on ne veut plus le sauver)
  if (v === "-") return false;

  // Validation typée
  if (rawType === "number") {
    if (!numEl) return false;           // incohérent
    if (v === "") return false;         // vide interdit
    const n = Number(v.replace(",", "."));
    return Number.isFinite(n);
  }

  if (rawType === "bool") {
    if (!boolEl) return false;
    // on accepte uniquement "0" ou "1"
    return v === "0" || v === "1";
  }

  if (rawType === "string") {
    if (!strEl) return false;
    // string non vide (tu peux autoriser vide si tu veux, mais UX => on évite)
    return v.length > 0;
  }

  // Type inconnu : on bloque par sécurité
  return false;
}

/* ----------------------------
   Helpers: state from DOM (P1/P2)
-----------------------------*/
function getAlertEnabledFromCard(card) {
  const alertForm = card.querySelector('form[data-endpoint="alerting"]');
  const cb = alertForm?.querySelector('input[name="alert_enabled"]');
  return !!cb?.checked;
}

function getPausedFromCard(card) {
  const pauseForm = card.querySelector('form[data-endpoint="pause"]');
  const cb = pauseForm?.querySelector('input[name="paused"]');
  return !!cb?.checked;
}

// inactive rule aligned with server: (!alerting) OR paused
// Supporte 2 formulaires : alerting + pause.
// NOTE: checkbox décochée => clé absente de FormData => get("x") devient null.
function computeCardFlags(card, form, formData) {
  const endpoint = form?.dataset?.endpoint || "";

  let alertEnabled = getAlertEnabledFromCard(card);
  let paused = getPausedFromCard(card);

  if (endpoint === "alerting") {
    alertEnabled = formData.get("alert_enabled") === "1";
  } else if (endpoint === "pause") {
    paused = formData.get("paused") === "1";
  }

  const isInactive = (!alertEnabled) || paused;
  return { endpoint, alertEnabled, paused, isInactive };
}

/* ----------------------------
   Raw value helpers (data-raw-*)
   Source of truth for parsing (P5)
-----------------------------*/
function getRawValueFromDataset(card) {
  const t = (card?.dataset?.rawType || "none").toLowerCase();

  if (t === "number") {
    const v = String(card.dataset.rawNum || "").replace(",", ".");
    const n = parseFloat(v);
    return Number.isFinite(n) ? n : null;
  }

  if (t === "bool") {
    if (card.dataset.rawBool === "1") return true;
    if (card.dataset.rawBool === "0") return false;
    return null;
  }

  if (t === "string") {
    return String(card.dataset.rawStr || "");
  }

  return null;
}

function safeToggleClasses(card, isCritical) {
  card.classList.remove("critical", "normal");
  if (typeof isCritical === "boolean") {
    card.classList.toggle("critical", isCritical);
    card.classList.toggle("normal", !isCritical);
  }
}

/* ----------------------------
   Critical recompute (optional)
   (utile uniquement si/qd tu ajoutes l'édition de seuil)
-----------------------------*/
function updateCriticalState(form) {
  const formData = new FormData(form);
  const card = form.closest(".metric-card, .service-card");
  if (!card) return;

  // NO_DATA => on ne calcule pas de critical/normal
  if (isNoDataCard(card)) {
    applyUnknownState(card, true);
    return;
  } else {
    applyUnknownState(card, false);
  }

  const { isInactive } = computeCardFlags(card, form, formData);

  // inactive => no critical/normal
  if (isInactive) {
    card.classList.remove("critical", "normal");
    return;
  }

  let isCritical = false;

  // NEW: UI seuil envoie value_num/value_bool/value_str + comparison
  const valueNumInput = form.querySelector('input[name="value_num"]');
  const valueBoolInput = form.querySelector('select[name="value_bool"]');
  const valueStrInput = form.querySelector('input[name="value_str"]');

  const currentRaw = getRawValueFromDataset(card);

  if (valueBoolInput) {
    const currentValue =
      typeof currentRaw === "boolean"
        ? currentRaw
        : parseBooleanFromDisplay(card.querySelector(".metric-last-value")?.textContent);

    const thresholdBool = formData.get("value_bool") === "1";
    const comparison = (formData.get("comparison") || "eq").toLowerCase();

    isCritical = (comparison === "ne")
      ? (currentValue !== thresholdBool)
      : (currentValue === thresholdBool);
  } else if (valueNumInput) {
    const currentValue =
      typeof currentRaw === "number"
        ? currentRaw
        : parseFloat(String(card.querySelector(".metric-last-value")?.textContent || "").replace(",", "."));

    const threshold = parseFloat(String(formData.get("value_num") || "").replace(",", "."));
    const comparison = (formData.get("comparison") || "gt").toLowerCase();

    if (!isNaN(currentValue) && !isNaN(threshold)) {
      switch (comparison) {
        case "gt": isCritical = currentValue > threshold; break;
        case "lt": isCritical = currentValue < threshold; break;
        case "eq": isCritical = currentValue === threshold; break;
        case "ge": isCritical = currentValue >= threshold; break;
        case "le": isCritical = currentValue <= threshold; break;
        case "ne": isCritical = currentValue !== threshold; break;
        default:   isCritical = currentValue > threshold;
      }
    }
  } else if (valueStrInput) {
    const currentValue =
      typeof currentRaw === "string"
        ? currentRaw
        : String(card.querySelector(".metric-last-value")?.textContent || "");

    const thresholdStr = String(formData.get("value_str") || "");
    const comparison = (formData.get("comparison") || "eq").toLowerCase();

    if (comparison === "contains") isCritical = currentValue.includes(thresholdStr);
    else if (comparison === "ne") isCritical = currentValue !== thresholdStr;
    else isCritical = currentValue === thresholdStr;
  } else {
    // pas d'inputs seuil => ne rien toucher
    return;
  }

  safeToggleClasses(card, isCritical);
}

function updateThresholdBadge(card) {
  if (!card) return;

  const badge = card.querySelector(".threshold-undefined");
  if (!badge) return;

  const isAlertEnabled = !card.classList.contains("inactive");

  const thresholdForm = card.querySelector('form[data-endpoint="threshold"]');
  const thresholdExists =
    thresholdForm?.dataset.thresholdExists === "1";

  const alertPill = card.querySelector(
    ".metric-meta .pill.state-on, .metric-meta .pill.state-off"
  );

  // --------------------------------------------------
  // Si un seuil existe → on retire complètement la pill
  // --------------------------------------------------
  if (thresholdExists) {
    badge.remove();
    return;
  }

  // --------------------------------------------------
  // Seuil non défini → message dépend de l’état alerte
  // --------------------------------------------------
  if (isAlertEnabled) {
    badge.textContent = "À définir";
    badge.classList.add("warning");
    badge.classList.remove("state-off");
  } else {
    badge.textContent = "Non défini";
    badge.classList.remove("warning");
    badge.classList.add("state-off");
  }

  // --------------------------------------------------
  // Mise à jour du libellé Alerte 🔔 / 🔕 (local à la card)
  // --------------------------------------------------
  if (alertPill) {
    alertPill.textContent = isAlertEnabled ? "Alerte 🔔" : "Alerte 🔕";
  }
}


/* ----------------------------
   Machine view initialization
   À ajouter dans machines.js
-----------------------------*/
function initMachineView() {
  console.log("🔄 Initialisation vue machine");
  
  // 1. Mise à jour des formulaires de seuil
  if (typeof window.updateThresholdUI === "function") {
    document.querySelectorAll('form[data-endpoint="threshold"]').forEach(window.updateThresholdUI);
  }
  
  // 2. Mise à jour des badges de seuil
  if (typeof window.updateThresholdBadge === "function") {
    document.querySelectorAll(".metric-card").forEach(window.updateThresholdBadge);
  }
  
  // 3. Trouver les éléments de filtres
  const searchEl = document.getElementById("metricSearch");
  const hideEl = document.getElementById("hideInactive");
  const advEl = document.getElementById("showAdvanced");
  const machineId = searchEl?.dataset?.machineId;
  
  // 4. Vérifier que les éléments existent
  if (!searchEl || !hideEl) {
    console.warn("⚠️ Éléments de filtres non disponibles, réessai dans 50ms");
    setTimeout(initMachineView, 50);
    return;
  }
  
  console.log("✅ Éléments de filtres trouvés");
  
  // 5. Forcer "Avancées" à false par défaut (sauf si restauré depuis cookie)
  if (advEl && !advEl.dataset.initialized) {
    advEl.checked = false;
    advEl.dataset.initialized = "true";
  }
  
  // 6. Restaurer les filtres depuis cookie
  if (machineId && typeof window.restoreFiltersFromCookie === "function") {
    try {
      window.restoreFiltersFromCookie(machineId);
    } catch (e) {
      console.warn("❌ Erreur restauration filtres, utilisation par défaut:", e);
      // Appliquer filtrage avec valeurs par défaut
      if (typeof window.filterMetrics === "function") {
        setTimeout(() => window.filterMetrics(), 10);
      }
    }
  } else {
    // Pas d'ID machine, juste filtrer avec valeurs par défaut
    if (typeof window.filterMetrics === "function") {
      setTimeout(() => window.filterMetrics(), 10);
    }
  }
  
  // 7. Ajouter les écouteurs d'événements pour les filtres (une seule fois)
  if (searchEl && !searchEl.dataset.listenersAdded) {
    searchEl.addEventListener("input", () => {
      if (typeof window.filterMetrics === "function") window.filterMetrics();
      if (typeof window.saveFiltersToCookie === "function") window.saveFiltersToCookie();
    });
    searchEl.dataset.listenersAdded = "true";
  }
  
  if (hideEl && !hideEl.dataset.listenersAdded) {
    hideEl.addEventListener("change", () => {
      if (typeof window.filterMetrics === "function") window.filterMetrics();
      if (typeof window.saveFiltersToCookie === "function") window.saveFiltersToCookie();
    });
    hideEl.dataset.listenersAdded = "true";
  }
  
  if (advEl && !advEl.dataset.listenersAdded) {
    advEl.addEventListener("change", () => {
      if (typeof window.filterMetrics === "function") window.filterMetrics();
      if (typeof window.saveFiltersToCookie === "function") window.saveFiltersToCookie();
    });
    advEl.dataset.listenersAdded = "true";
  }
}

// Exposer la fonction au scope global
window.initMachineView = initMachineView;


// à appeler au chargement si tu as des formulaires déjà rendus
// Modifiez l'initialisation dans DOMContentLoaded
// document.addEventListener("DOMContentLoaded", () => {
//   document.querySelectorAll("form").forEach(updateThresholdUI);

//   const searchEl = document.getElementById("metricSearch");
//   const machineId = searchEl?.dataset?.machineId;
//   const advEl = document.getElementById("showAdvanced");

//   // FORCER le filtre avancé à false par défaut
//   if (advEl && advEl.checked) {
//     advEl.checked = false;
//   }

//   if (machineId) {
//     try { 
//       restoreFiltersFromCookie(machineId); 
//     } catch (_) {
//       // En cas d'erreur, forcer les valeurs par défaut
//       filterMetrics();
//     }
//   } else {
//     try { filterMetrics(); } catch (_) {}
//   }
// });



window.restoreFiltersFromCookie = restoreFiltersFromCookie;
window.saveFiltersToCookie = saveFiltersToCookie;
window.getSearchText = getSearchText;
window.filterMetrics = filterMetrics;
window.autoSave = autoSave;
window.applySuggestedThreshold = applySuggestedThreshold;