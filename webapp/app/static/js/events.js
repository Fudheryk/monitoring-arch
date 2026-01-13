// events.js — gestion du fragment "Historique des événements"
// Objectif : filtres + recherche + pagination côté client sur les lignes du tableau.
// Supporte désormais : incident / notification / machine.

console.log("📦 events.js chargé");

let eventsRows = [];
let eventsCurrentPage = 1;
let eventsPageSize = 20;

// ──────────────────────────────
// Helpers
// ──────────────────────────────

/**
 * Convertit la "ligne" (row.dataset.*) en une valeur de statut normalisée
 * qui matche exactement les <option value="..."> du select #events-status-filter.
 *
 * On ne renvoie une valeur "mappedStatus" QUE pour les types concernés :
 * - incident      -> incident_open / incident_resolved
 * - notification  -> notif_info / notif_warning / notif_error / notif_critical
 * - machine       -> machine_registered / machine_unregistered (nouveau)
 *
 * Pour les autres cas : "" (aucun statut mappable)
 */
function mapRowStatus(row) {
  const kind = (row.dataset.kind || "").toLowerCase();
  const rawStatus = (row.dataset.status || "").toLowerCase();
  const subkind = (row.dataset.subkind || "").toLowerCase();
  const sev = (row.dataset.severity || "").toLowerCase();

  // ---- INCIDENTS ----
  // Dans ton HTML : data-status = "ouvert" / "resolu" (ou variants)
  // NB: si un jour tu changes en "OPEN"/"RESOLVED", on accepte aussi.
  if (kind === "incident") {
    if (rawStatus === "open" || rawStatus === "ouvert") return "incident_open";
    if (rawStatus === "resolved" || rawStatus === "resolu" || rawStatus === "résolu")
      return "incident_resolved";
    return "";
  }

  // ---- NOTIFICATIONS ----
  // Dans ton HTML : data-severity = "info|warning|error|critical"
  // On mappe vers les valeurs du select : notif_info, notif_warning, etc.
  if (kind === "notification") {
    if (sev === "info") return "notif_info";
    if (sev === "warning") return "notif_warning";
    if (sev === "error") return "notif_error";
    if (sev === "critical") return "notif_critical";
    return "";
  }

  // ---- MACHINES ----
  // Dans ton HTML : data-kind="machine" + data-subkind="registered|unregistered"
  if (kind === "machine") {
    if (subkind === "registered") return "machine_registered";
    if (subkind === "unregistered") return "machine_unregistered";
    return "";
  }

  return "";
}

/**
 * Retourne la liste des lignes filtrées selon les filtres actifs (type / statut / recherche).
 */
function getFilteredRows() {
  if (!eventsRows || eventsRows.length === 0) return [];

  const kindFilter = document.getElementById("events-kind-filter");
  const statusFilter = document.getElementById("events-status-filter");
  const searchInput = document.getElementById("events-search");

  const kindVal = kindFilter ? kindFilter.value : "all";
  const statusVal = statusFilter ? statusFilter.value : "all";
  const searchVal = searchInput ? searchInput.value.trim().toLowerCase() : "";

  return eventsRows.filter((row) => {
    const kind = (row.dataset.kind || "").toLowerCase();
    const mappedStatus = mapRowStatus(row);

    // Filtre Type
    if (kindVal !== "all" && kind !== kindVal) return false;

    // Filtre Statut
    // Remarque : ton select "Statut" ne contient pour l’instant que incidents + notifications.
    // Si tu ajoutes des options machine_*, ça fonctionnera directement.
    if (statusVal !== "all") {
      // Si la ligne n'a pas de statut mappable, on l'exclut quand un statut est demandé
      if (!mappedStatus) return false;
      if (mappedStatus !== statusVal) return false;
    }

    // Recherche full-text
    if (searchVal) {
      const text = (row.textContent || "").toLowerCase();
      if (!text.includes(searchVal)) return false;
    }

    return true;
  });
}

/**
 * Met à jour l'info "Page X / Y"
 */
function updatePageInfo(page, total) {
  const info = document.getElementById("events-page-info");
  if (info) info.textContent = `Page ${page} / ${total}`;
}

/**
 * Met à jour le compteur "X résultat(s)"
 */
function updateVisibleCount(count) {
  const visibleEl = document.getElementById("events-visible");
  if (visibleEl) visibleEl.textContent = `${count} résultat(s)`;
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
  if (eventsCurrentPage > totalPages) eventsCurrentPage = totalPages;

  const start = (eventsCurrentPage - 1) * eventsPageSize;
  const end = start + eventsPageSize;

  // On cache tout
  eventsRows.forEach((row) => {
    row.style.display = "none";
  });

  // On affiche uniquement les lignes filtrées qui appartiennent à la page courante
  filtered.forEach((row, idx) => {
    if (idx >= start && idx < end) row.style.display = "";
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
  if (tbody.dataset.eventsBound === "true") return;
  tbody.dataset.eventsBound = "true";

  console.log("🔧 events.js: binding pagination/filters sur le fragment events");

  // On mémorise toutes les lignes
  eventsRows = Array.from(tbody.querySelectorAll(".event-row"));
  eventsCurrentPage = 1;

  const pageSizeSelect = document.getElementById("events-page-size-select");
  const prevBtn = document.getElementById("events-prev");
  const nextBtn = document.getElementById("events-next");
  const kindFilter = document.getElementById("events-kind-filter");
  const statusFilter = document.getElementById("events-status-filter");
  const searchInput = document.getElementById("events-search");

  // Taille de page
  if (pageSizeSelect) {
    pageSizeSelect.addEventListener("change", () => {
      const val = parseInt(pageSizeSelect.value, 10);
      eventsPageSize = Number.isFinite(val) ? val : 20;
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

  // Filtre type (incident / notification / machine / tous)
  if (kindFilter) {
    kindFilter.addEventListener("change", () => {
      eventsCurrentPage = 1;
      applyEventsPagination();
    });
  }

  // Filtre statut
  // NB: ton HTML ne propose pas encore machine_registered/unregistered.
  // Si tu ajoutes ces options, ce handler fonctionne déjà.
  if (statusFilter) {
    statusFilter.addEventListener("change", () => {
      eventsCurrentPage = 1;
      applyEventsPagination();
    });
  }

  // Recherche texte temps réel
  if (searchInput) {
    let searchDebounce = null;
    searchInput.addEventListener("input", () => {
      clearTimeout(searchDebounce);
      searchDebounce = setTimeout(() => {
        eventsCurrentPage = 1;
        applyEventsPagination();
      }, 200);
    });
  }

  // Page initiale : on lit la valeur du select ou 20 par défaut
  const initialVal = pageSizeSelect ? parseInt(pageSizeSelect.value, 10) : 20;
  eventsPageSize = Number.isFinite(initialVal) ? initialVal : 20;

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
