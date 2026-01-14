/* webapp/app/static/js/main.js
 *
 * But :
 * - Charger des fragments HTML (sites/machines/events/settings) via fetch() dans #content
 * - Gérer les redirections d’auth (401/303/opaqueredirect)
 * - Afficher une topbar de progression pendant les requêtes
 * - Initialiser certains comportements après injection HTML (sites auto-refresh, settings/events bind, vue machine)
 *
 * ⚠️ IMPORTANT : ce fichier doit être sans erreur de syntaxe, sinon loadView n’est jamais défini
 * et les onclick="loadView(...)" cassent.
 */

/**
 * Charge une vue (fragment HTML) et l’injecte dans le DOM, avec :
 * - annulation des requêtes précédentes (AbortController)
 * - gestion des redirections auth (/login)
 * - replay automatique si cookies rafraîchis (X-Auth-Refreshed)
 * - init post-injection (machine view, settings, events, etc.)
 *
 * IMPORTANT (fix titre machines) :
 * - si on est déjà sur la page machines (#machines existe), alors au clic machine
 *   on injecte le fragment *dans* #machines-content (ou #machines) au lieu de tout remplacer.
 *   => le <h2> 🖥️ Liste des serveurs reste visible.
 * - sinon (navigation depuis navbar), on injecte dans #content comme avant.
 */
function loadView(view, machineId = null) {

  // Ferme le menu responsive s'il est ouvert
  document.querySelector('.nav-left')?.classList.remove('open');
 
  // ------------------------------------------------------------
  // 0) Abort précédent fetch si on change vite
  // ------------------------------------------------------------
  if (window.currentViewFetchCtrl) {
    try { window.currentViewFetchCtrl.abort(); } catch (_) {}
    // si on enchaîne rapidement, la barre précédente doit se cacher
    window.TopBar?.abort?.();
  }
  window.currentViewFetchCtrl = new AbortController();

  // ------------------------------------------------------------
  // 1) Démarre la barre de chargement
  // ------------------------------------------------------------
  window.TopBar?.start();

  // ------------------------------------------------------------
  // 2) Construire l'URL de fragment
  // ------------------------------------------------------------
  let url = `/fragment/${view}`;
  if (machineId) url = `/fragment/machine/${machineId}`; // détail machine

  console.log("🔄 Chargement de la vue:", view, machineId ? `(machine=${machineId})` : "");

  // Options fetch
  const fetchOpts = {
    redirect: "manual", // on détecte nous-mêmes les redirections (login)
    headers: { "X-Requested-With": "fetch", "Cache-Control": "no-store" },
    signal: window.currentViewFetchCtrl.signal,
    credentials: "include",
  };

  // Permet de rejouer le fetch si le serveur a rafraîchi les cookies (X-Auth-Refreshed)
  const replay = async (u) => {
    const r = await fetch(u, fetchOpts);
    if (!r.ok) throw new Error(`HTTP ${r.status} (replay)`);
    return r;
  };

  // ------------------------------------------------------------
  // 3) Exécution async encapsulée (pour finally fiable)
  // ------------------------------------------------------------
  (async () => {
    let redirectedToAnotherView = false;

    try {
      let res = await fetch(url, fetchOpts);

      // --------------------------------------------------------
      // 3.1) Gestion redirections/auth
      // --------------------------------------------------------
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

      // Cookies rafraîchis => rejouer la requête
      if (res.status === 200 && res.headers.get("X-Auth-Refreshed") === "1") {
        console.log("🔄 Cookies rafraîchis (AJAX) → replay du fetch");
        res = await replay(url);
      }

      // Si le navigateur a suivi une redirection malgré redirect:manual
      if (res.redirected && new URL(res.url).pathname === "/login") {
        console.warn("🔐 fetch.redirected vers /login → redirection pleine page");
        window.TopBar?.abort();
        window.location.href = "/login";
        return null;
      }

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      // --------------------------------------------------------
      // 3.2) Lire le HTML
      // --------------------------------------------------------
      const html = await res.text();

      // --------------------------------------------------------
      // 3.3) Choix du conteneur d’injection (FIX titre machines)
      // --------------------------------------------------------
      // Cas A : on clique une machine alors qu'on est déjà sur la vue machines
      // => on remplace seulement le contenu dynamique (#machines-content)
      //    pour garder <h2> 🖥️ Liste des serveurs
      const isMachineDetailFetch = (view === "machine" && !!machineId);

      const machinesSection = document.getElementById("machines"); // existe seulement si la vue machines est affichée
      const machinesContent = document.getElementById("machines-content"); // existe si tu as appliqué Fix 2

      let container = null;

      if (isMachineDetailFetch && machinesSection) {
        // ✅ Fix 2 : injecter dans #machines-content si possible, sinon fallback #machines
        container = machinesContent || machinesSection;
      } else {
        // Navigation "normale" : injecter tout le fragment dans #content
        container = document.getElementById("content");
      }

      if (!container) throw new Error("Conteneur d'injection introuvable (#machines-content/#machines/#content)");

      container.innerHTML = html;

      // --------------------------------------------------------
      // 3.4) Post-injection : init machine view si présente
      // --------------------------------------------------------
      // NOTE: Si on injecte dans #machines-content, le DOM machine est bien mis à jour,
      // et initMachineView doit être rappelée.
      const machineRoot = document.querySelector("#machine-view[data-machine-id]");
      if (machineRoot) {
        const mid = machineRoot.dataset.machineId;

        // Laisse le DOM se stabiliser avant d'initialiser (inputs/listeners)
        setTimeout(() => {
          if (typeof window.initMachineView === "function") {
            window.initMachineView();
          } else {
            // Fallback minimal
            if (typeof window.restoreFiltersFromCookie === "function") {
              try { window.restoreFiltersFromCookie(mid); } catch (_) {}
            }
            if (typeof window.filterMetrics === "function") {
              setTimeout(() => window.filterMetrics(), 50);
            }
          }
        }, 50);
      }

      // --------------------------------------------------------
      // 3.5) Seuils : UI/badges après injection
      // --------------------------------------------------------
      if (typeof window.updateThresholdUI === "function") {
        document
          .querySelectorAll('form[data-endpoint="threshold"]')
          .forEach(window.updateThresholdUI);
      }

      if (typeof window.updateThresholdBadge === "function") {
        document.querySelectorAll(".metric-card").forEach(window.updateThresholdBadge);
      } else if (typeof updateThresholdBadge === "function") {
        // compat si tu l’as aussi en global non namespacé
        document.querySelectorAll(".metric-card").forEach(updateThresholdBadge);
      }

      // --------------------------------------------------------
      // 3.6) Hooks globaux (délégation d’événements, etc.)
      // --------------------------------------------------------
      try { window.initializeEventListeners?.(); }
      catch (e) { console.warn("⚠️ initializeEventListeners a échoué :", e); }

      // --------------------------------------------------------
      // 3.7) Auto-refresh sites
      // --------------------------------------------------------
      if (view === "sites") window.SitesView?.startAutoRefresh?.();
      else window.SitesView?.stopAutoRefresh?.();

      // --------------------------------------------------------
      // 3.8) Bind settings / events
      // --------------------------------------------------------
      if (view === "settings" && window.SettingsView?.bind) {
        console.log("🔧 Binding settings.js pour la vue settings");
        window.SettingsView.bind();
      }
      if (view === "events" && window.EventsView?.bind) {
        console.log("🧾 Binding events.js pour la vue events");
        window.EventsView.bind();
      }

      // --------------------------------------------------------
      // 3.9) Rafraîchir timestamps après injection
      // --------------------------------------------------------
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

      // --------------------------------------------------------
      // 3.10) Scroll top (optionnel : seulement si navigation complète)
      // --------------------------------------------------------
      // Si on change juste le détail machine, tu peux choisir de NE PAS scroll.
      // Ici: on scroll uniquement quand on change de vue entière (pas machine detail).
      if (!isMachineDetailFetch) {
        window.scrollTo(0, 0);
      }

      return null;
    } catch (error) {
      if (error?.name === "AbortError") {
        // abort = normal quand on change vite de vue
        window.TopBar?.abort();
        return;
      }

      window.TopBar?.abort();
      console.error("❌ Erreur lors du chargement de la vue:", error);

      // Fallback : on essaye d’afficher l’erreur dans #content (ou machines-content si dispo)
      const fallback =
        document.getElementById("machines-content") ||
        document.getElementById("content");

      if (fallback) fallback.innerHTML = "<p>Erreur lors du chargement</p>";
    } finally {
      if (!redirectedToAnotherView) window.TopBar?.done();
    }
  })();
}


function toggleMenu() {
  document.querySelector('.nav-left').classList.toggle('open');
}

/**
 * TopBar : petite barre de progression en haut de page
 * - start() : affiche la barre et simule une progression jusqu’à ~90%
 * - done()  : termine à 100% puis fade out
 * - abort() : cache immédiatement (ex: changement de vue / erreur)
 */
window.TopBar = (() => {
  const el = () => document.getElementById("nm-topbar");
  let timer = null;
  let progress = 0;

  function start() {
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

  function done() {
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

  function abort() {
    const bar = el();
    if (!bar) return;
    stopTimer();
    bar.classList.remove("loading");
    bar.style.width = "0%";
    bar.style.opacity = "0";
    setTimeout(() => { bar.style.opacity = ""; }, 180);
  }

  function stopTimer() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  return { start, done, abort };
})();

/**
 * Boot :
 * - Home doit ouvrir "sites" par défaut (comportement historique)
 * - On peut ensuite naviguer via la navbar (onclick)
 * - On rafraîchit aussi les timestamps si la fonction existe
 */
document.addEventListener("DOMContentLoaded", () => {

  // 1. Bouton menu hamburger
  const menuToggle = document.getElementById("menu-toggle");
  if (menuToggle) {
    menuToggle.addEventListener("click", toggleMenu);
  }
  
  // 2. Gestion des clics sur les liens de navigation
  // Remplacer les onclick="loadView('...')" supprimés
  document.addEventListener("click", (e) => {
    const link = e.target.closest("[data-load-view]");
    if (link) {
      e.preventDefault();
      const view = link.dataset.loadView;
      const machineId = link.dataset.machineId || null;
      loadView(view, machineId);
    }
  });

  console.log("🏗️ DOM chargé, chargement vue sites");
  loadView("sites");

  const doRefresh = window.Timefmt?.refreshLastChecks || window.refreshLastChecks;
  if (typeof doRefresh === "function") doRefresh();
});

// Expose les fonctions globalement pour les onclick
window.loadView = loadView;
window.toggleMenu = toggleMenu;
