/* webapp/app/static/main.js */

/* webapp/app/static/main.js */

function loadView(view, machineId = null) {
  if (window.currentViewFetchCtrl) {
    try { window.currentViewFetchCtrl.abort(); } catch (_) {}
  }
  window.currentViewFetchCtrl = new AbortController();

  let url = `/fragment/${view}`;
  if (machineId) url = `/fragment/machine/${machineId}`;

  console.log("🔄 Chargement de la vue:", view);

  const fetchOpts = {
    redirect: "manual",
    headers: { "X-Requested-With": "fetch", "Cache-Control": "no-store" },
    signal: window.currentViewFetchCtrl.signal,
  };

  const replay = async (u) => {
    const r = await fetch(u, fetchOpts);
    if (!r.ok) throw new Error(`HTTP ${r.status} (replay)`);
    return r;
  };

  fetch(url, fetchOpts)
    .then(async (res) => {
      if (res.type === "opaqueredirect" || res.status === 0) {
        console.warn("🔐 opaqueredirect/status 0 → redirection /login");
        window.location.href = "/login";
        return null;
      }

      if (res.status === 303) {
        console.warn("🔐 303 reçu (auth guard) → redirection /login");
        window.location.href = "/login";
        return null;
      }

      if (res.status === 401 || res.headers.get("X-Auth-Redirect") === "1") {
        console.warn("🔐 401 / X-Auth-Redirect → redirection /login");
        window.location.href = "/login";
        return null;
      }

      if (res.status === 200 && res.headers.get("X-Auth-Refreshed") === "1") {
        console.log("🔄 Cookies rafraîchis (AJAX) → replay du fetch");
        res = await replay(url);
      }

      if (res.redirected && new URL(res.url).pathname === "/login") {
        console.warn("🔐 fetch.redirected vers /login → redirection pleine page");
        window.location.href = "/login";
        return null;
      }

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const html = await res.text();
      const container = document.getElementById("content");
      if (!container) throw new Error("#content introuvable");
      container.innerHTML = html;

      // Mise à jour des formulaires de seuil après injection HTML
      if (typeof window.updateThresholdUI === "function") {
        document
          .querySelectorAll('form[data-endpoint="threshold"]')
          .forEach(window.updateThresholdUI);
      }

      if (typeof updateThresholdBadge === "function") {
        document.querySelectorAll(".metric-card").forEach(updateThresholdBadge);
      }

      // Auto redirect to first machine
      if (view === "machines" && !machineId) {
        const firstMachineBtn = document.querySelector("aside button.machine-button, .grid [data-machine-id]");
        if (firstMachineBtn) {
          const firstMachineId =
            firstMachineBtn.dataset.machineId || firstMachineBtn.getAttribute("data-machine-id");
          if (firstMachineId) {
            console.log("🔁 Redirection automatique vers machine ID:", firstMachineId);
            loadView("machine", firstMachineId);
            return null;
          }
        }
      }

      // Init hooks for injected HTML (site form, etc.)
      try { window.initializeEventListeners?.(); }
      catch (e) { console.warn("⚠️ initializeEventListeners a échoué :", e); }

      // Sites auto-refresh
      if (view === "sites") window.SitesView?.startAutoRefresh?.();
      else window.SitesView?.stopAutoRefresh?.();

      // Settings/events binders
      if (view === "settings" && window.SettingsView?.bind) {
        console.log("🔧 Binding settings.js pour la vue settings");
        window.SettingsView.bind();
      }
      if (view === "events" && window.EventsView?.bind) {
        console.log("🧾 Binding events.js pour la vue events");
        window.EventsView.bind();
      }

      // Refresh "(il y a …)" timestamps after injection
      requestAnimationFrame(() => {
        if (document.querySelector(".nm-last-check")) {
          try {
            const doRefresh = window.Timefmt?.refreshLastChecks || window.refreshLastChecks;
            if (typeof doRefresh === "function") doRefresh();
          } catch (e) {
            console.warn("⚠️ Erreur lors du rafraîchissement des timestamps :", e);
          }
        }
      });

      // ====================================================
      // CORRECTION : Initialisation spécifique à la vue machine
      // ====================================================
      if (view === "machine" && machineId) {
        // Initialiser la vue machine après un délai pour garantir que le DOM est prêt
        setTimeout(() => {
          // Vérifier d'abord si initMachineView existe et l'appeler
          if (typeof window.initMachineView === "function") {
            window.initMachineView();
          } else {
            // Fallback : restaurer les filtres directement
            console.warn("⚠️ initMachineView non disponible, restauration directe des filtres");
            if (typeof window.restoreFiltersFromCookie === "function") {
              try { 
                window.restoreFiltersFromCookie(machineId);
              } catch (e) { 
                console.warn("⚠️ restoreFiltersFromCookie a échoué :", e);
                // En cas d'échec, appliquer le filtrage par défaut
                if (typeof window.filterMetrics === "function") {
                  setTimeout(() => window.filterMetrics(), 50);
                }
              }
            } else {
              // Si restoreFiltersFromCookie n'existe pas, appliquer filtrage par défaut
              if (typeof window.filterMetrics === "function") {
                setTimeout(() => window.filterMetrics(), 50);
              }
            }
          }
        }, 100); // Délai augmenté à 100ms pour garantir la stabilité du DOM
      }
      // ====================================================
      // Fin de la correction
      // ====================================================

      window.scrollTo(0, 0);
      return null;
    })
    .catch((error) => {
      console.error("❌ Erreur lors du chargement de la vue:", error);
      const container = document.getElementById("content");
      if (container) container.innerHTML = "<p>Erreur lors du chargement</p>";
    });
}

document.addEventListener("DOMContentLoaded", () => {
  console.log("🏗️ DOM chargé, chargement vue sites");
  loadView("sites");

  const doRefresh = window.Timefmt?.refreshLastChecks || window.refreshLastChecks;
  if (typeof doRefresh === "function") doRefresh();
});

window.loadView = loadView;
