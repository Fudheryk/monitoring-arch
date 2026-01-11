/* webapp/app/static/main.js */

/* webapp/app/static/main.js */

function loadView(view, machineId = null) {
  if (window.currentViewFetchCtrl) {
    try { window.currentViewFetchCtrl.abort(); } catch (_) {}
    // si on enchaîne rapidement, la barre précédente doit se cacher
    window.TopBar?.abort?.();
  }
  window.currentViewFetchCtrl = new AbortController();

  window.TopBar?.start();

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

// On encapsule pour pouvoir faire un finally (done/abort) fiable.
  (async () => {
    let redirectedToAnotherView = false;
    try {
      let res = await fetch(url, fetchOpts);
      if (res.type === "opaqueredirect" || res.status === 0) {
        console.warn("🔐 opaqueredirect/status 0 → redirection /login");
        window.TopBar?.abort();
        window.location.href = "/login";
        return null;
      }

      if (res.status === 303) {
        console.warn("🔐 303 reçu (auth guard) → redirection /login");
        window.TopBar?.abort();
        window.location.href = "/login";
        return null;
      }

      if (res.status === 401 || res.headers.get("X-Auth-Redirect") === "1") {
        console.warn("🔐 401 / X-Auth-Redirect → redirection /login");
        window.TopBar?.abort();
        window.location.href = "/login";
        return null;
      }

      if (res.status === 200 && res.headers.get("X-Auth-Refreshed") === "1") {
        console.log("🔄 Cookies rafraîchis (AJAX) → replay du fetch");
        res = await replay(url);
      }

      if (res.redirected && new URL(res.url).pathname === "/login") {
        console.warn("🔐 fetch.redirected vers /login → redirection pleine page");
        window.TopBar?.abort();
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
            // on ne "done" pas : une nouvelle vue part tout de suite
            redirectedToAnotherView = true;
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
    } catch (error) {
      if (error?.name === "AbortError") {
        // abort = normal quand on change vite de vue
        window.TopBar?.abort();
        return;
      }
      window.TopBar?.abort();
      console.error("❌ Erreur lors du chargement de la vue:", error);
      const container = document.getElementById("content");
      if (container) container.innerHTML = "<p>Erreur lors du chargement</p>";
    } finally {
      // Fin “normale” : si on n'a pas relancé une autre vue, on termine la barre.
      if (!redirectedToAnotherView) window.TopBar?.done();
    }
  })();
}

window.TopBar = (() => {
  const el = () => document.getElementById("nm-topbar");
  let timer = null;
  let progress = 0;

  function start(){
    const bar = el();
    if (!bar) return;

    // reset
    stopTimer();
    progress = 0;
    bar.classList.add("loading");
    bar.style.width = "0%";

    // petite latence pour éviter flicker si réponse très rapide
    requestAnimationFrame(() => {
      progress = 10;
      bar.style.width = progress + "%";
    });

    // fake progress (monte doucement jusqu'à ~90%)
    timer = setInterval(() => {
      if (!window.currentViewFetchCtrl) return;
      if (progress >= 90) return;
      const step = progress < 60 ? 6 : progress < 80 ? 3 : 1;
      progress = Math.min(90, progress + step);
      bar.style.width = progress + "%";
    }, 180);
  }

  function done(){
    const bar = el();
    if (!bar) return;

    stopTimer();
    progress = 100;
    bar.style.width = "100%";

    // fade out après avoir atteint 100
    setTimeout(() => {
      bar.classList.remove("loading");
      bar.style.opacity = "0";
      // remet à 0% après disparition
      setTimeout(() => { bar.style.width = "0%"; bar.style.opacity = ""; }, 180);
    }, 150);
  }

  function abort(){
    // si abort volontaire, soit on cache immédiatement,
    // soit on laisse la nouvelle requête relancer start()
    const bar = el();
    if (!bar) return;
    stopTimer();
    bar.classList.remove("loading");
    bar.style.width = "0%";
    bar.style.opacity = "0";
    setTimeout(() => { bar.style.opacity = ""; }, 180);
  }

  function stopTimer(){
    if (timer) clearInterval(timer);
    timer = null;
  }

  return { start, done, abort };
})();


document.addEventListener("DOMContentLoaded", () => {
  console.log("🏗️ DOM chargé, chargement vue sites");
  loadView("sites");

  const doRefresh = window.Timefmt?.refreshLastChecks || window.refreshLastChecks;
  if (typeof doRefresh === "function") doRefresh();
});

window.loadView = loadView;
