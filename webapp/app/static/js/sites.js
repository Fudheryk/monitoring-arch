/**
 * sites.js — gestion du fragment "Sites monitorés"
 * Appelle les routes proxy WebApp:
 *   - POST   /webapi/http-targets            → crée une cible
 *   - PATCH  /webapi/http-targets/{id}       → toggle is_active
 *   - DELETE /webapi/http-targets/{id}       → supprime
 *
 * Le fragment sites.html fournit:
 *   - <form id="site-form"> avec <input name="url">
 *   - .pause-btn[data-id][data-active], .delete-btn[data-id]
 */

console.log("📦 sites.js chargé");

let isSubmitting = false;

// ──────────────────────────────
// Pause auto-refresh pendant saisie (UX)
// ──────────────────────────────
let sitesIsEditing = false;

// ═══════════════════════════════════════════════════════════════════════════════
// 🔹 Auto-refresh de la vue Sites toutes les 30 secondes
// ═══════════════════════════════════════════════════════════════════════════════
let sitesRefreshTimer = null;

/* ----------------------------
   Init hooks for injected HTML
-----------------------------*/
function initializeEventListeners() {
  // Après injection du fragment sites : on s’assure que la délégation + guards sont en place
  initSitesDelegation();
  bindSitesEditGuards();
}

function isUserEditingSiteForm() {
  const form = document.getElementById("site-form");
  const input = document.getElementById("site-url");
  if (!form || !input) return false;

  // si le focus est dans le formulaire, ou si on a commencé à saisir
  const active = document.activeElement;
  const focusInForm = !!(active && form.contains(active));
  const hasDraft = (input.value || "").trim().length > 0;
  return sitesIsEditing || focusInForm || hasDraft;
}

function bindSitesEditGuards() {
  const root = document.getElementById("content");
  if (!root || root.dataset.sitesEditGuards === "on") return;
  root.dataset.sitesEditGuards = "on";

  // on considère "édition" dès qu'on focus ou qu'on tape dans l'input
  root.addEventListener("focusin", (e) => {
    if (e.target && (e.target.id === "site-url" || e.target.closest("#site-form"))) {
      sitesIsEditing = true;
    }
  });

  root.addEventListener("input", (e) => {
    if (e.target && e.target.id === "site-url") {
      sitesIsEditing = true;
    }
  });

  // quand on quitte le formulaire, on relâche le flag (léger délai)
  root.addEventListener("focusout", (e) => {
    if (e.target && e.target.closest && e.target.closest("#site-form")) {
      setTimeout(() => {
        const form = document.getElementById("site-form");
        const active = document.activeElement;
        if (!form || !(active && form.contains(active))) {
          const input = document.getElementById("site-url");
          // si draft vide, on peut reprendre l'auto-refresh
          if (!input || (input.value || "").trim() === "") {
            sitesIsEditing = false;
          }
        }
      }, 150);
    }
  });
}

function startSitesAutoRefresh() {
  // Nettoie l'ancien timer si présent
  if (sitesRefreshTimer) {
    clearInterval(sitesRefreshTimer);
  }

  // Lance le refresh toutes les 30 secondes
  sitesRefreshTimer = setInterval(() => {
    // Vérifie qu'on est toujours sur la vue "sites"
    const siteGrid = document.getElementById('site-grid');
    if (siteGrid) {
      // ❗ Ne pas auto-refresh pendant la saisie/édition du formulaire
      if (isUserEditingSiteForm()) {
        console.log("⏸️ Auto-refresh Sites suspendu (saisie en cours)");
        return;
      }
      console.log("🔄 Auto-refresh de la vue Sites");
      loadView('sites');
    } else {
      // Si on n'est plus sur la vue sites, arrête le timer
      stopSitesAutoRefresh();
    }
  }, 30_000); // 30 secondes

  console.log("⏰ Auto-refresh Sites activé (30s)");
}

function stopSitesAutoRefresh() {
  if (sitesRefreshTimer) {
    clearInterval(sitesRefreshTimer);
    sitesRefreshTimer = null;
    console.log("⏹️ Auto-refresh Sites arrêté");
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// 🔹 Validation locale de l'URL
// ═══════════════════════════════════════════════════════════════════════════════
function validateSiteUrl() {
  const input = document.getElementById("site-url");
  const error = document.getElementById("url-error");
  const url = input.value.trim();
  const isValid = /^https?:\/\/[\w.-]+\.[a-z]{2,}(\/.*)?$/i.test(url);
  input.classList.toggle("invalid", !isValid);
  error.style.display = isValid ? "none" : "block";
  return isValid;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 🔹 Ajout d'un site  → POST /webapi/http-targets
// ═══════════════════════════════════════════════════════════════════════════════
async function addSite(e) {
  e.preventDefault();
  if (isSubmitting) return; // Anti double-submit

  // 1) Validation UX immédiate
  if (!validateSiteUrl()) return;

  const form = document.getElementById("site-form");
  if (!form) {
    console.error("❌ Formulaire introuvable");
    return;
  }
  const btn = form.querySelector('button[type="submit"]');

  // 2) Verrou UI
  isSubmitting = true;
  if (btn) { btn.disabled = true; btn.textContent = "⏳ Ajout..."; }

  try {
    // 3) Normalisation d'URL : on garde uniquement l'origine (https://domaine.tld)
    const rawUrl = form.url.value.trim();
    let cleanUrl;
    try {
      const u = new URL(rawUrl);
      cleanUrl = u.origin.replace(/\/+$/, ""); // retire un slash final éventuel
    } catch {
      // Si jamais l'objet URL lève, on retombe sur l'ancienne normalisation
      cleanUrl = rawUrl.replace(/[?#].*$/, "").replace(/\/+$/, "");
    }

    // 4) Appel proxy (le proxy complétera method/expected_status/etc.)
    const res = await fetch("/webapi/http-targets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: cleanUrl })
    });

    if (res.ok) {
      console.log("✅ Site créé");
    } else if (res.status === 409) {
      // Conflit : déjà existant
      const data = await res.json().catch(() => ({}));
      const existingId = data?.detail?.existing_id;
      alert("Ce site est déjà surveillé." + (existingId ? ` (id: ${existingId})` : ""));
    } else if (res.status === 422) {
      alert("URL invalide pour l'API.");
    } else {
      const data = await res.json().catch(() => ({}));
      alert("Erreur: " + (data.message || "Impossible d'ajouter le site."));
    }
  } catch (err) {
    console.error("❌ Erreur réseau :", err);
    alert("Erreur de connexion : " + (err?.message || err));
  } finally {
    // 5) Déverrou UI + refresh
    if (btn) { btn.disabled = false; btn.textContent = "➕ Ajouter"; }
    isSubmitting = false;

    // Optionnel UX : reset et focus
    form.reset();
    document.getElementById("site-url")?.focus();

    loadView("sites"); // recharge la liste
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// 🔹 Pause / Reprise  → PATCH /webapi/http-targets/{id}
// ═══════════════════════════════════════════════════════════════════════════════
function togglePause(id, isActive) {
  fetch(`/webapi/http-targets/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_active: !isActive })
  })
    .then((res) => {
      if (!res.ok) console.warn("⚠️ Erreur lors du toggle :", res.status);
    })
    .finally(() => loadView("sites"));
}

// ═══════════════════════════════════════════════════════════════════════════════
// 🔹 Suppression  → DELETE /webapi/http-targets/{id}
// ═══════════════════════════════════════════════════════════════════════════════
function confirmDelete(id) {
  if (!confirm("⚠️ Confirmer la suppression de ce site ?")) return;

  fetch(`/webapi/http-targets/${id}`, { method: "DELETE" })
    .then((res) => {
      if (res.ok) {
        console.log("🗑️ Site supprimé :", id);
        loadView("sites");
      } else {
        alert("Erreur lors de la suppression.");
      }
    })
    .catch((err) => {
      console.error("❌ Erreur réseau :", err);
      alert("Erreur de connexion : " + err.message);
    });
}

// ═══════════════════════════════════════════════════════════════════════════════
// 🔹 Gestion des événements via délégation
// ═══════════════════════════════════════════════════════════════════════════════
function initSitesDelegation() {
  const root = document.getElementById("content");
  if (!root || root.dataset.sitesDelegation === "on") return;
  root.dataset.sitesDelegation = "on";
  console.log("🔧 Délégation activée pour Sites");

  // Clics (pause / suppression)
  root.addEventListener("click", (e) => {
    const pauseBtn = e.target.closest(".pause-btn");
    if (pauseBtn) {
      const id = pauseBtn.dataset.id;
      const isActive = pauseBtn.dataset.active === "true";
      togglePause(id, isActive);
      return;
    }

    const delBtn = e.target.closest(".delete-btn");
    if (delBtn) {
      const id = delBtn.dataset.id;
      confirmDelete(id);
      return;
    }
  });

  // Soumission du formulaire d'ajout
  root.addEventListener("submit", (e) => {
    const form = e.target.closest("#site-form");
    if (form) {
      addSite(e);
    }
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// 🔹 Activation unique après chargement du DOM
// ═══════════════════════════════════════════════════════════════════════════════
document.addEventListener("DOMContentLoaded", () => {
  initSitesDelegation();
  bindSitesEditGuards();
});

// ═══════════════════════════════════════════════════════════════════════════════
// 🔹 Expose les fonctions pour que main.js puisse les appeler
// ═══════════════════════════════════════════════════════════════════════════════
window.SitesView = {
  startAutoRefresh: startSitesAutoRefresh,
  stopAutoRefresh: stopSitesAutoRefresh
};

window.initializeEventListeners = initializeEventListeners;