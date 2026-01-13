// settings.js — gestion du fragment "Paramètres des alertes"
//
// - Validation email + webhook Slack (front)
// - Sauvegarde automatique vers /webapi/settings
//   * debounce pendant la saisie (800 ms après la dernière frappe)
//   * sauvegarde immédiate sur blur / changement de checkbox
// - Indicateurs visuels de sauvegarde
// - Validation en temps réel
// - Messages d'erreur contextuels

console.log("📦 settings.js chargé");

let settingsDebounceTimer = null;
let lastChangeAt = 0;
const SETTINGS_DEBOUNCE_MS = 800;
let isSaving = false;
let lastSaveTime = null;

// ──────────────────────────────
// Indicateurs visuels de sauvegarde
// ──────────────────────────────

/**
 * Affiche un indicateur de sauvegarde en cours
 */
function showSavingIndicator() {
  isSaving = true;
  const indicator = document.getElementById("save-indicator");
  if (indicator) {
    indicator.textContent = "💾 Sauvegarde...";
    indicator.className = "save-indicator saving";
    indicator.style.display = "inline-block";
  }
}

/**
 * Affiche un indicateur de succès
 */
function showSaveSuccess() {
  isSaving = false;
  lastSaveTime = Date.now();
  const indicator = document.getElementById("save-indicator");
  if (indicator) {
    indicator.textContent = "✓ Sauvegardé";
    indicator.className = "save-indicator success";
    indicator.style.display = "inline-block";
    
    // Disparaît après 3 secondes
    setTimeout(() => {
      if (Date.now() - lastSaveTime >= 2900) {
        indicator.style.display = "none";
      }
    }, 3000);
  }
}

/**
 * Affiche un indicateur d'erreur
 */
function showSaveError(message = "Erreur lors de la sauvegarde") {
  isSaving = false;
  const indicator = document.getElementById("save-indicator");
  if (indicator) {
    indicator.textContent = `✗ ${message}`;
    indicator.className = "save-indicator error";
    indicator.style.display = "inline-block";
    
    // Disparaît après 5 secondes
    setTimeout(() => {
      indicator.style.display = "none";
    }, 5000);
  }
}

/**
 * Crée l'indicateur de sauvegarde s'il n'existe pas
 */
function ensureSaveIndicator() {
  if (!document.getElementById("save-indicator")) {
    const form = document.getElementById("alert-config-form");
    if (form) {
      const indicator = document.createElement("div");
      indicator.id = "save-indicator";
      indicator.className = "save-indicator";
      indicator.style.display = "none";
      form.insertBefore(indicator, form.lastChild);
    }
  }
}

// ──────────────────────────────
// Helpers de validation
// ──────────────────────────────

/**
 * Valide un champ email.
 * - Vide => considéré comme OK (optionnel, on l'enverra en null côté API)
 * - Sinon => regex simple
 */
function validateEmailField(input, showSuccess = false) {
  if (!input) return true;

  const email = input.value.trim();
  const errorMsg = document.getElementById("email-error");
  const successMsg = document.getElementById("email-success");
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  // Email vide -> pas d'erreur (optionnel)
  if (email === "") {
    input.classList.remove("input-error", "input-success");
    if (errorMsg) errorMsg.style.display = "none";
    if (successMsg) successMsg.style.display = "none";
    return true;
  }

  const isValid = emailRegex.test(email);

  if (!isValid) {
    input.classList.add("input-error");
    input.classList.remove("input-success");
    if (errorMsg) {
      errorMsg.textContent = "Adresse email invalide";
      errorMsg.style.display = "block";
    }
    if (successMsg) successMsg.style.display = "none";
    return false;
  } else {
    input.classList.remove("input-error");
    if (showSuccess) {
      input.classList.add("input-success");
      if (successMsg) {
        successMsg.textContent = "✓ Email valide";
        successMsg.style.display = "block";
      }
    }
    if (errorMsg) errorMsg.style.display = "none";
    return true;
  }
}

/**
 * Valide un webhook Slack.
 * Attendu : https://hooks.slack.com/services/XXX/YYY/ZZZ
 *
 * - Vide => OK (optionnel, envoyé en null)
 * - Sinon :
 *   - https obligatoire
 *   - domaine hooks.slack.com
 *   - path commençant par /services/
 *   - segments alphanumériques / _ / -
 */
function validateSlackWebhook(input, showSuccess = false) {
  if (!input) return true;

  const url = input.value.trim();
  const errorMsg = document.getElementById("slack-error");
  const successMsg = document.getElementById("slack-success");

  // Vide -> OK (optionnel)
  if (url === "") {
    input.classList.remove("input-error", "input-success");
    if (errorMsg) errorMsg.style.display = "none";
    if (successMsg) successMsg.style.display = "none";
    return true;
  }

  let isValid = false;
  let errorMessage = "Webhook Slack invalide";

  try {
    const urlObj = new URL(url);

    if (urlObj.protocol !== "https:") {
      errorMessage = "L'URL doit utiliser HTTPS";
    } else if (urlObj.hostname !== "hooks.slack.com") {
      errorMessage = "Le domaine doit être hooks.slack.com";
    } else if (!urlObj.pathname.startsWith("/services/")) {
      errorMessage = "Le chemin doit commencer par /services/";
    } else {
      const pathParts = urlObj.pathname.split("/").filter((part) => part !== "");
      // pathParts ex: ["services", "Txxx", "Bxxx", "dmfPDJ..."]
      if (pathParts.length < 3 || pathParts[0] !== "services") {
        errorMessage = "Format de webhook invalide";
      } else {
        const pathRegex = /^[A-Za-z0-9_-]+$/;
        const isValidPath = pathParts.slice(1).every((part) => pathRegex.test(part));
        if (!isValidPath) {
          errorMessage = "Caractères invalides dans l'URL";
        } else {
          isValid = true;
        }
      }
    }
  } catch (e) {
    errorMessage = "URL invalide";
  }

  if (!isValid) {
    input.classList.add("input-error");
    input.classList.remove("input-success");
    if (errorMsg) {
      errorMsg.textContent = errorMessage;
      errorMsg.style.display = "block";
    }
    if (successMsg) successMsg.style.display = "none";
    return false;
  } else {
    input.classList.remove("input-error");
    if (showSuccess) {
      input.classList.add("input-success");
      if (successMsg) {
        successMsg.textContent = "✓ Webhook valide";
        successMsg.style.display = "block";
      }
    }
    if (errorMsg) errorMsg.style.display = "none";
    return true;
  }
}

/**
 * Valide un nom de canal Slack (optionnel).
 * Accepte "canal" ou "#canal"
 * Règles: minuscules, chiffres, tirets, underscores. Pas d'espaces.
 * Longueur max: 15 (aligné avec maxlength HTML).
 */
function validateSlackChannel(input, showSuccess = false) {
  if (!input) return true;

  const raw = (input.value || "").trim();
  const errorId = "slack-channel-error";
  const successId = "slack-channel-success";

  // Récupère ou crée les blocs d'erreur/succès (sans modifier ton HTML existant)
  let errorMsg = document.getElementById(errorId);
  if (!errorMsg) {
    errorMsg = document.createElement("div");
    errorMsg.id = errorId;
    errorMsg.className = "error-msg";
    errorMsg.style.display = "none";
    input.insertAdjacentElement("afterend", errorMsg);
  }

  let successMsg = document.getElementById(successId);
  if (!successMsg) {
    successMsg = document.createElement("div");
    successMsg.id = successId;
    successMsg.className = "success-msg";
    successMsg.style.display = "none";
    // on le met après l'erreur
    errorMsg.insertAdjacentElement("afterend", successMsg);
  }

  // Vide => OK (optionnel)
  if (raw === "") {
    input.classList.remove("input-error", "input-success");
    errorMsg.style.display = "none";
    successMsg.style.display = "none";
    return true;
  }

  // Normalisation: si l'utilisateur tape "#canal" on garde, sinon on peut préfixer
  const normalized = raw.startsWith("#") ? raw : `#${raw}`;

  // Enlève le # pour valider le "nom"
  const name = normalized.slice(1);

  // Longueur: alignée sur maxlength (15). Tu peux ajuster si besoin.
  const maxLen = parseInt(input.getAttribute("maxlength") || "15", 10);

  let isValid = true;
  let errorMessage = "Nom de canal invalide";

  if (name.length === 0) {
    isValid = false;
    errorMessage = "Le canal ne peut pas être uniquement '#'";
  } else if (name.length > maxLen) {
    isValid = false;
    errorMessage = `Le canal doit faire ${maxLen} caractères max`;
  } else if (!/^[a-z0-9_-]+$/.test(name)) {
    isValid = false;
    errorMessage = "Utilisez uniquement a-z, 0-9, '_' ou '-' (pas d'espaces)";
  } else if (name.startsWith("-") || name.startsWith("_") || name.endsWith("-") || name.endsWith("_")) {
    // optionnel, mais évite des noms bizarres
    isValid = false;
    errorMessage = "Le canal ne doit pas commencer/finir par '-' ou '_'";
  }

  if (!isValid) {
    input.classList.add("input-error");
    input.classList.remove("input-success");
    errorMsg.textContent = errorMessage;
    errorMsg.style.display = "block";
    successMsg.style.display = "none";
    return false;
  }

  // OK
  input.classList.remove("input-error");
  if (showSuccess) {
    input.classList.add("input-success");
    successMsg.textContent = "";
    successMsg.style.display = "block";
  }
  errorMsg.style.display = "none";

  return true;
}

/**
 * Valide un champ numérique (minutes)
 */
function validateNumericField(input) {
  if (!input) return true;

  const value = input.value.trim();
  
  // Vide -> valeur par défaut 0
  if (value === "") {
    input.classList.remove("input-error");
    return true;
  }

  const numValue = parseInt(value, 10);
  
  // Vérifie que c'est un nombre positif
  if (isNaN(numValue) || numValue < 0) {
    input.classList.add("input-error");
    return false;
  }

  input.classList.remove("input-error");
  return true;
}

/**
 * Empêche la saisie de caractères non numériques dans un <input type="number">
 * (empêche e/E/+/- qui sont autorisés par certains navigateurs pour les floats)
 */
function restrictToDigits(evt) {
  const invalidChars = ["e", "E", "+", "-", ".", ","];
  if (invalidChars.includes(evt.key)) {
    evt.preventDefault();
  }
}

// ──────────────────────────────
// Sauvegarde (immédiate / debounce)
// ──────────────────────────────

/**
 * Sauvegarde immédiate des paramètres vers /webapi/settings.
 * Appelée:
 *   - après une pause de frappe (via debounce)
 *   - sur blur pour email/webhook
 *   - sur change pour les checkboxes
 */
async function saveSettingsNow() {
  const form = document.getElementById("alert-config-form");
  if (!form) {
    console.warn("⚠️ saveSettingsNow appelé sans formulaire présent");
    return;
  }

  // Empêche les sauvegardes multiples simultanées
  if (isSaving) {
    console.log("⏳ Sauvegarde déjà en cours, requête ignorée");
    return;
  }

  const emailInput  = document.getElementById("alert_email");
  const slackInput  = document.getElementById("alert_slack");
  const slackChanInput = document.getElementById("alert_slack_channel_name");
  const graceInput  = document.getElementById("grace_minutes");
  const remindInput = document.getElementById("reminder_interval");
  const groupCb     = document.getElementById("group_alerts");
  const suppressCb  = document.getElementById("suppress_resolution_alert");

  // 1) Validation front
  const emailOk  = validateEmailField(emailInput, true);
  const slackOk  = validateSlackWebhook(slackInput, true);
  const chanOk   = validateSlackChannel(slackChanInput, true);
  const graceOk  = validateNumericField(graceInput);
  const remindOk = validateNumericField(remindInput);

  if (!emailOk || !slackOk || !chanOk || !graceOk || !remindOk) {
    console.warn("⚠️ Validation front échouée, requête API annulée");
    showSaveError("Vérifiez les champs invalides");
    return;
  }

  // 2) Construction du payload (en secondes côté API)
  const notification_email = (emailInput?.value || "").trim() || null;
  const slack_webhook_url  = (slackInput?.value || "").trim() || null;

  const graceMinutes    = graceInput?.value ? parseInt(graceInput.value, 10) : 0;
  const reminderMinutes = remindInput?.value ? parseInt(remindInput.value, 10) : 0;

  let slack_channel_name = (slackChanInput?.value || "").trim() || null;
  if (slack_channel_name && !slack_channel_name.startsWith("#")) {
    slack_channel_name = "#" + slack_channel_name;
  }

  const payload = {
    notification_email,
    slack_webhook_url,
    slack_channel_name,
    grace_period_seconds: Math.max(0, graceMinutes) * 60,
    reminder_notification_seconds: Math.max(0, reminderMinutes) * 60,
    alert_grouping_enabled: !!(groupCb && groupCb.checked),
    // checkbox "Ne pas recevoir l'alerte de résolution"
    // => notify_on_resolve = NOT suppress_resolution_alert
    notify_on_resolve: !(suppressCb && suppressCb.checked),
  };

  console.log("📤 Envoi settings:", payload);
  showSavingIndicator();

  try {
    const res = await fetch("/webapi/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      console.error("❌ Erreur API settings:", res.status, data);
      
      let errorMsg = "Erreur lors de la sauvegarde";
      if (data.detail) {
        errorMsg = data.detail;
      } else if (res.status === 400) {
        errorMsg = "Données invalides";
      } else if (res.status === 500) {
        errorMsg = "Erreur serveur";
      }
      
      showSaveError(errorMsg);
      return;
    }

    console.log("✅ Paramètres sauvegardés");
    showSaveSuccess();
  } catch (err) {
    console.error("❌ Erreur réseau settings:", err);
    showSaveError("Erreur de connexion");
  }
}

/**
 * Version "debounce" : relance un timer à chaque frappe,
 * ne déclenche saveSettingsNow() qu'après SETTINGS_DEBOUNCE_MS ms
 * sans nouvelle saisie.
 */
function saveSettingsDebounced() {
  lastChangeAt = Date.now();
  if (settingsDebounceTimer) {
    clearTimeout(settingsDebounceTimer);
  }
  settingsDebounceTimer = setTimeout(saveSettingsNow, SETTINGS_DEBOUNCE_MS);
}

// ──────────────────────────────
// Validation en temps réel (sans sauvegarder)
// ──────────────────────────────

/**
 * Valide un champ pendant la saisie (sans afficher le succès)
 */
function validateFieldOnInput(input, validator) {
  return () => {
    validator(input, false);
  };
}

// ──────────────────────────────
// Binding sur le fragment settings
// ──────────────────────────────

/**
 * Attache les listeners sur les champs du formulaire #alert-config-form.
 * Appelée:
 *   - au DOMContentLoaded (si la vue settings est déjà là)
 *   - après loadView("settings") via SettingsView.bind()
 */
function bindSettingsFragment() {
  const form = document.getElementById("alert-config-form");
  if (!form) {
    // On n'est pas sur la vue settings
    return;
  }

  // Évite le double-binding si on recharge plusieurs fois la vue
  if (form.dataset.settingsBound === "true") {
    return;
  }
  form.dataset.settingsBound = "true";

  console.log("🔧 settings.js: binding listeners sur le formulaire de settings");

  // Crée l'indicateur de sauvegarde
  ensureSaveIndicator();

  const emailInput  = document.getElementById("alert_email");
  const slackInput  = document.getElementById("alert_slack");
  const slackChanInput = document.getElementById("alert_slack_channel_name");
  const graceInput  = document.getElementById("grace_minutes");
  const remindInput = document.getElementById("reminder_interval");
  const groupCb     = document.getElementById("group_alerts");
  const suppressCb  = document.getElementById("suppress_resolution_alert");

  // ---- Champs texte (email / Slack) ----

  if (emailInput) {
    // Validation en temps réel pendant la saisie
    emailInput.addEventListener("input", () => {
      validateFieldOnInput(emailInput, validateEmailField)();
      saveSettingsDebounced();
    });
    
    // Validation finale + sauvegarde sur blur
    emailInput.addEventListener("blur", () => {
      validateEmailField(emailInput, true);
      // évite double sauvegarde si blur arrive juste après la dernière frappe
      if (Date.now() - lastChangeAt > 300) {
        saveSettingsNow();
      }
    });

    // Validation initiale
    validateEmailField(emailInput, false);
  }

  if (slackInput) {
    // Validation en temps réel pendant la saisie
    slackInput.addEventListener("input", () => {
      validateFieldOnInput(slackInput, validateSlackWebhook)();
      saveSettingsDebounced();
    });
    
    // Validation finale + sauvegarde sur blur
    slackInput.addEventListener("blur", () => {
      validateSlackWebhook(slackInput, true);
      // évite double sauvegarde si blur arrive juste après la dernière frappe
      if (Date.now() - lastChangeAt > 300) {
        saveSettingsNow();
      }
    });

    // Validation initiale
    validateSlackWebhook(slackInput, false);
  }

  if (slackChanInput) {
    slackChanInput.addEventListener("input", () => {
      validateSlackChannel(slackChanInput, false); // pas de ✓ pendant la saisie
      saveSettingsDebounced();
    });

    slackChanInput.addEventListener("blur", () => {
      validateSlackChannel(slackChanInput, true); // affiche ✓ si tu veux
      if (Date.now() - lastChangeAt > 300) {
        saveSettingsNow();
      }
    });

    // Validation initiale
    validateSlackChannel(slackChanInput, false);
  }

  // ---- Champs numériques (minutes) ----

  if (graceInput) {
    graceInput.addEventListener("keydown", restrictToDigits);
    graceInput.addEventListener("input", () => {
      validateNumericField(graceInput);
      saveSettingsDebounced();
    });
    graceInput.addEventListener("blur", saveSettingsNow);

    // Validation initiale
    validateNumericField(graceInput);
  }

  if (remindInput) {
    remindInput.addEventListener("keydown", restrictToDigits);
    remindInput.addEventListener("input", () => {
      validateNumericField(remindInput);
      saveSettingsDebounced();
    });
    remindInput.addEventListener("blur", saveSettingsNow);

    // Validation initiale
    validateNumericField(remindInput);
  }

  // ---- Cases à cocher : change = sauvegarde immédiate ----

  if (groupCb) {
    groupCb.addEventListener("change", saveSettingsNow);
  }
  if (suppressCb) {
    suppressCb.addEventListener("change", saveSettingsNow);
  }

  // On désactive complètement le submit, il n'y a pas de bouton
  form.addEventListener("submit", (e) => e.preventDefault());
}

/**
 * Nettoie les bindings quand on quitte la vue
 */
function unbindSettingsFragment() {
  const form = document.getElementById("alert-config-form");
  if (form) {
    form.dataset.settingsBound = "false";
  }
  
  // Annule tout timer en cours
  if (settingsDebounceTimer) {
    clearTimeout(settingsDebounceTimer);
    settingsDebounceTimer = null;
  }
}

// ──────────────────────────────
// Initialisation globale
// ──────────────────────────────

// Si la vue settings est déjà présente au premier chargement
document.addEventListener("DOMContentLoaded", () => {
  bindSettingsFragment();
});

// Exposé pour main.js (appelé après loadView("settings"))
window.SettingsView = {
  bind: bindSettingsFragment,
  unbind: unbindSettingsFragment,
  saveNow: saveSettingsNow,
  debouncedSave: saveSettingsDebounced,
};