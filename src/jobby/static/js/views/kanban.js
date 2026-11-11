import { getActiveKanbanCount, getState, subscribe } from '../state.js';

const KANBAN_STAGES = ['saved', 'applied', 'interview', 'offer'];

export function renderKanbanView(snapshot = getState()) {
  const summaryEl = document.getElementById('kanban-shell-summary');
  const apps = snapshot.applications || [];
  const activeCount = getActiveKanbanCount(apps);

  if (summaryEl) {
    summaryEl.textContent = `${activeCount} candidature${activeCount > 1 ? 's' : ''} active${activeCount > 1 ? 's' : ''}`;
  }

  const stageCounts = Object.assign(Object.create(null), {
    saved: 0,
    applied: 0,
    interview: 0,
    offer: 0,
  });
  apps.forEach((app) => {
    const st = String(app?.status || 'saved').trim().toLowerCase();
    if (Object.hasOwn(stageCounts, st)) {
      stageCounts[st] += 1;
    }
  });

  KANBAN_STAGES.forEach((stage) => {
    const counterEl = document.querySelector(`[data-kanban-col-count="${stage}"]`);
    if (counterEl) {
      counterEl.textContent = String(stageCounts[stage]);
    }
  });
}

export function initKanbanView() {
  let prevApps = getState().applications;
  subscribe((snapshot) => {
    if (snapshot.applications !== prevApps) {
      prevApps = snapshot.applications;
      renderKanbanView(snapshot);
    }
  });
  renderKanbanView(getState());
}
