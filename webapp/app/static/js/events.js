// events.js — gestion du fragment "Historique des événements"

console.log("📦 events.js chargé");

let eventsRows = [];
let eventsCurrentPage = 1;
let eventsPageSize = 20;

// ──────────────────────────────
// Helpers
// ──────────────────────────────

// Retourne la liste des lignes filtrées selon les filtres actifs
function getFilteredRows() {
  if (!eventsRows || eventsRows.length === 0) return [];

  const kindFilter   = document.getElementById("events-kind-filter");
  const statusFilter = document.getElementById("events-status-filter");
  const searchInput  = document.getElementById("events-search");

  const kindVal   = kindFilter   ? kindFilter.value   : "all";
  const statusVal = statusFilter ? statusFilter.value : "all";
  const searchVal = searchInput  ? searchInput.value.trim().toLowerCase() : "";

  return eventsRows.filter((row) => {
    const kind = (row.dataset.kind || "").toLowerCase();      // "incident" | "notification"
    const rawStatus = (row.dataset.status || "").toLowerCase(); // "open"/"resolved" ou "info"/"warning"/...

    let mappedStatus = rawStatus;

    // ---- Mapping pour les INCIDENTS ----
    // data-status vient de ev.status → "OPEN" / "RESOLVED"
    if (kind === "incident") {
      if (rawStatus === "open" || rawStatus === "ouvert") {
        mappedStatus = "incident_open";
      } else if (
        rawStatus === "resolved" ||
        rawStatus === "resolu" ||
        rawStatus === "résolu"
      ) {
        mappedStatus = "incident_resolved";
      }
    }

    // ---- Mapping pour les NOTIFICATIONS ----
    // data-status = severity → "info" / "warning" / "error" / "critical"
    if (kind === "notification") {
      const sev = (row.dataset.severity || "").toLowerCase();
      if (sev === "info") mappedStatus = "notif_info";
      else if (sev === "warning") mappedStatus = "notif_warning";
      else if (sev === "error") mappedStatus = "notif_error";
      else if (sev === "critical") mappedStatus = "notif_critical";
    }

    // Filtre Type (incident / notification / tous)
    if (kindVal !== "all" && kind !== kindVal) {
      return false;
    }

    // Filtre Statut (avec les valeurs du select)
    if (statusVal !== "all" && mappedStatus !== statusVal) {
      return false;
    }

    // Recherche texte full-text
    if (searchVal) {
      const text = row.textContent.toLowerCase();
      if (!text.includes(searchVal)) {
        return false;
      }
    }

    return true;
  });
}

// Met à jour l'info "Page X / Y"
function updatePageInfo(page, total) {
  const info = document.getElementById("events-page-info");
  if (info) {
    info.textContent = `Page ${page} / ${total}`;
  }
}

// Met à jour le compteur "X résultat(s)" si tu as un span dédié
function updateVisibleCount(count) {
  const visibleEl = document.getElementById("events-visible");
  if (visibleEl) {
    visibleEl.textContent = `${count} résultat(s)`;
  }
}

// ──────────────────────────────
// Pagination principale
// ──────────────────────────────

function applyEventsPagination() {
  if (!eventsRows || eventsRows.length === 0) return;

  const filtered = getFilteredRows();

  // Cas "Tout" → pas de pagination, on montre tout ce qui matche
  if (eventsPageSize === 0) {
    eventsRows.forEach((row) => {
      row.style.display = filtered.includes(row) ? "" : "none";
    });

    updatePageInfo(1, 1);
    updateVisibleCount(filtered.length);
    return;
  }

  // Nombre de pages sur la base des lignes filtrées
  const totalPages = Math.max(1, Math.ceil(filtered.length / eventsPageSize));
  if (eventsCurrentPage > totalPages) {
    eventsCurrentPage = totalPages;
  }

  const start = (eventsCurrentPage - 1) * eventsPageSize;
  const end   = start + eventsPageSize;

  // On cache tout
  eventsRows.forEach((row) => {
    row.style.display = "none";
  });

  // On affiche uniquement les lignes filtrées qui appartiennent à la page courante
  filtered.forEach((row, idx) => {
    if (idx >= start && idx < end) {
      row.style.display = "";
    }
  });

  updatePageInfo(eventsCurrentPage, totalPages);
  updateVisibleCount(filtered.length);
}

// ──────────────────────────────
// Binding du fragment
// ──────────────────────────────

function bindEventsFragment() {
  const tbody = document.getElementById("events-tbody");
  if (!tbody) return; // on n'est pas sur la vue events

  // Éviter double-binding si loadView("events") est rappelé
  if (tbody.dataset.eventsBound === "true") {
    return;
  }
  tbody.dataset.eventsBound = "true";

  console.log("🔧 events.js: binding pagination/filters sur le fragment events");

  // On mémorise toutes les lignes
  eventsRows = Array.from(tbody.querySelectorAll(".event-row"));
  eventsCurrentPage = 1;

  const pageSizeSelect = document.getElementById("events-page-size-select");
  const prevBtn        = document.getElementById("events-prev");
  const nextBtn        = document.getElementById("events-next");
  const kindFilter     = document.getElementById("events-kind-filter");
  const statusFilter   = document.getElementById("events-status-filter"); // optionnel
  const searchInput    = document.getElementById("events-search");        // optionnel

  // Taille de page
  if (pageSizeSelect) {
    pageSizeSelect.addEventListener("change", () => {
      const val = parseInt(pageSizeSelect.value, 10);
      eventsPageSize = isNaN(val) ? 20 : val;
      eventsCurrentPage = 1;
      applyEventsPagination();
    });
  }

  // Bouton "Précédent"
  if (prevBtn) {
    prevBtn.addEventListener("click", () => {
      if (eventsPageSize === 0) return; // "Tout" → une seule page logique
      if (eventsCurrentPage > 1) {
        eventsCurrentPage -= 1;
        applyEventsPagination();
      }
    });
  }

  // Bouton "Suivant"
  if (nextBtn) {
    nextBtn.addEventListener("click", () => {
      if (eventsPageSize === 0) return; // "Tout" → pas de next
      const filtered = getFilteredRows();
      const totalPages = Math.max(1, Math.ceil(filtered.length / eventsPageSize));
      if (eventsCurrentPage < totalPages) {
        eventsCurrentPage += 1;
        applyEventsPagination();
      }
    });
  }

  // Filtre type (incident / notification / tous)
  if (kindFilter) {
    kindFilter.addEventListener("change", () => {
      eventsCurrentPage = 1;
      applyEventsPagination();
    });
  }

  // Filtre statut (si présent dans le HTML)
  if (statusFilter) {
    statusFilter.addEventListener("change", () => {
      eventsCurrentPage = 1;
      applyEventsPagination();
    });
  }

  // Recherche texte temps réel (si input présent)
  if (searchInput) {
    let searchDebounce = null;
    searchInput.addEventListener("input", () => {
      clearTimeout(searchDebounce);
      searchDebounce = setTimeout(() => {
        eventsCurrentPage = 1;
        applyEventsPagination();
      }, 200); // petit debounce pour ne pas recalculer à chaque frappe
    });
  }

  // Page initiale : on lit la valeur du select ou 20 par défaut
  const initialVal = pageSizeSelect ? parseInt(pageSizeSelect.value, 10) : 20;
  eventsPageSize = isNaN(initialVal) ? 20 : initialVal;

  applyEventsPagination();
}

// ──────────────────────────────
// Intégration SPA
// ──────────────────────────────

// Si la vue events est déjà présente au premier paint
document.addEventListener("DOMContentLoaded", () => {
  bindEventsFragment();
});

// Pour que main.js puisse rebinder après loadView("events")
window.EventsView = {
  bind: bindEventsFragment,
};
