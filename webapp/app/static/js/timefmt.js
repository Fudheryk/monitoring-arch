/**
 * timefmt.js — Utilitaire " …" pour affichage des timestamps
 * Compatible <script> classique (non-module)
 * 
 * Expose :
 *  - window.Timefmt.humanizeAgo(ageSec)       → formatte un âge en texte lisible
 *  - window.Timefmt.refreshLastChecks()       → met à jour tous les <span class="nm-last-check">
 *  - window.Timefmt.startAutoRefresh(ms)      → lance un rafraîchissement périodique
 *  - window.humanizeAgo(ageSec)               → alias global pour compatibilité legacy
 */

(function (global) {
  'use strict';

  // Namespace pour éviter les collisions
  const ns = (global.Timefmt ||= {});

  // ═══════════════════════════════════════════════════════════════════════════════
  // 🔹 Formatte un âge en secondes en texte lisible " X h/j/mois/ans"
  // ═══════════════════════════════════════════════════════════════════════════════
  function humanizeAgo(ageSec) {
    // Validation : doit être un nombre positif et fini
    if (ageSec == null || ageSec < 0 || !Number.isFinite(ageSec)) {
      return "";
    }

    // Conversion de base
    const minutes = ageSec / 60;
    const hours = minutes / 60;
    const days = hours / 24;
    const months = days / 30;
    const years = days / 365;

    // Formatage progressif
    if (ageSec < 60) {
      return ` ${Math.floor(ageSec)} s`;
    }
    if (minutes < 60) {
      return ` ${Math.floor(minutes)} min`;
    }
    if (hours < 24) {
      return ` ${Math.floor(hours)} h`;
    }
    if (days < 30) {
      return ` ${Math.floor(days)} j`;
    }
    if (months < 12) {
      return ` ${Math.floor(months)} mois`;
    }
    return ` ${Math.floor(years)} an${years >= 2 ? "s" : ""}`;
  }


  // ═══════════════════════════════════════════════════════════════════════════════
  // 🔹 Calcule l'âge en secondes à partir des attributs data-* de l'élément
  // ═══════════════════════════════════════════════════════════════════════════════
  // Supporte deux modes :
  //  1. data-ts : timestamp absolu (ms ou s) → calcule l'âge par rapport à maintenant
  //  2. data-age : âge initial en secondes → "vieillit" côté client depuis data-startMs
  function computeAgeSec(el, nowMs) {
    // Mode 1 : Timestamp absolu
    const tsAttr = el.dataset.ts;
    if (tsAttr !== undefined) {
      let ts = Number(tsAttr);
      if (!Number.isFinite(ts)) return null;
      
      // Normalise les timestamps en secondes → millisecondes
      if (ts < 3e10) ts *= 1000;
      
      return Math.max(0, Math.floor((nowMs - ts) / 1000));
    }

    // Mode 2 : Âge initial + vieillissement client
    const initialAge = Number(el.dataset.age);
    if (!Number.isFinite(initialAge)) return null;

    // Mémorise le timestamp de départ (première lecture)
    if (!el.dataset.startMs) {
      el.dataset.startMs = String(nowMs);
    }

    const startMs = Number(el.dataset.startMs);
    const elapsedSec = Math.max(0, Math.floor((nowMs - startMs) / 1000));
    
    return initialAge + elapsedSec;
  }

  // ═══════════════════════════════════════════════════════════════════════════════
  // 🔹 Met à jour tous les éléments .nm-last-check avec le texte "( …)"
  // ═══════════════════════════════════════════════════════════════════════════════
  function refreshLastChecks() {
    const now = Date.now();

    document.querySelectorAll(".nm-last-check, .nm-state-since").forEach((el) => {
      const ageSec = computeAgeSec(el, now);
      if (ageSec == null) return;

      const core = (humanizeAgo(ageSec) || "").trim(); // "2 min", "35 s", …

      let prefix = "";

      // Disable prefix on some elements (metrics)
      if (el.classList.contains("nm-no-prefix")) {
        el.textContent = core ? `${core}` : "";
        return;
      }

      if (el.classList.contains("nm-last-check")) {
        prefix = "CHECK "; // pour la dernière vérif
      } else if (el.classList.contains("nm-state-since")) {
        // remonte jusqu’au conteneur .site-status pour détecter l’état
        const statusEl = el.closest(".site-status");
        if (statusEl?.querySelector(".status-indicator.status-down")) {
          prefix = "KO ";
        } else {
          // par défaut, si status-down n’est pas présent → UP
          prefix = "UP ";
        }
      }

      el.textContent = core ? `${prefix}${core}` : "";
    });
  }


  // ═══════════════════════════════════════════════════════════════════════════════
  // 🔹 Lance un rafraîchissement automatique à intervalle régulier
  // ═══════════════════════════════════════════════════════════════════════════════
  function startAutoRefresh(intervalMs = 60_000) {
    // Si un timer existe déjà, on le nettoie pour éviter les doublons
    if (ns._timerId) {
      clearInterval(ns._timerId);
    }

    // Lance le timer
    ns._timerId = setInterval(refreshLastChecks, intervalMs);

    // Rafraîchit immédiatement (pas besoin d'attendre le premier tick)
    refreshLastChecks();
  }

  // ═══════════════════════════════════════════════════════════════════════════════
  // 🔹 Pause/reprise automatique selon la visibilité de l'onglet (économie de CPU)
  // ═══════════════════════════════════════════════════════════════════════════════
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      // Onglet redevenu visible → rafraîchit pour synchroniser
      refreshLastChecks();
    }
  });

  // ═══════════════════════════════════════════════════════════════════════════════
  // 🔹 Exposition des fonctions dans le namespace Timefmt
  // ═══════════════════════════════════════════════════════════════════════════════
  ns.humanizeAgo = humanizeAgo;
  ns.refreshLastChecks = refreshLastChecks;
  ns.startAutoRefresh = startAutoRefresh;
  ns.computeAgeSec = computeAgeSec; // Exposé pour tests/debug si nécessaire

  // ═══════════════════════════════════════════════════════════════════════════════
  // 🔹 Alias global pour compatibilité avec sites.js et autres scripts legacy
  // ═══════════════════════════════════════════════════════════════════════════════
  global.humanizeAgo = humanizeAgo;
  global.refreshLastChecks = refreshLastChecks;

})(window);