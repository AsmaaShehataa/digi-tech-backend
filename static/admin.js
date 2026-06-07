const projectsTableBody = document.getElementById("projects-table-body");
const totalProjectsEl = document.getElementById("total-projects");
const activeProjectsEl = document.getElementById("active-projects");
const completedProjectsEl = document.getElementById("completed-projects");
const completedRevenueEl = document.getElementById("completed-revenue");
const pendingPaymentsEl = document.getElementById("pending-payments");
const overduePaymentsEl = document.getElementById("overdue-payments");
const portfolioProgressEl = document.getElementById("portfolio-progress");
const deadlineListEl = document.getElementById("deadline-list");
const form = document.getElementById("project-form");
const formTitle = document.getElementById("form-title");
const formFeedback = document.getElementById("form-feedback");
const projectIdField = document.getElementById("project-id");
const saveProjectButton = document.getElementById("save-project-button");
const cancelEditButton = document.getElementById("cancel-edit-button");
const addMilestoneButton = document.getElementById("add-milestone");
const milestonesContainer = document.getElementById("milestones-container");
const milestoneTemplate = document.getElementById("milestone-template");
const shareForm = document.getElementById("share-form");
const shareResult = document.getElementById("share-result");
const dashboardCurrencySelect = document.getElementById("dashboard-currency");
const exportCsvLink = document.getElementById("export-csv-link");
const exportJsonLink = document.getElementById("export-json-link");

let cachedProjects = [];

const formatterCache = new Map();
const getCurrencyFormatter = (currencyCode) => {
  const key = currencyCode || "USD";
  if (!formatterCache.has(key)) {
    formatterCache.set(
      key,
      new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: key,
        minimumFractionDigits: 2,
      })
    );
  }
  return formatterCache.get(key);
};

const formatCurrency = (value, currencyCode) => getCurrencyFormatter(currencyCode).format(Number(value || 0));

const resolveDashboardCurrency = (overview, projects) => {
  if (overview?.currency) return overview.currency;
  const projectCurrencies = new Set(projects.map((project) => project.currency).filter(Boolean));
  if (projectCurrencies.size === 1) {
    return [...projectCurrencies][0];
  }
  return "USD";
};

const statusLabelMap = {
  planned: "Planned",
  in_progress: "In progress",
  on_hold: "On hold",
  completed: "Completed",
  cancelled: "Cancelled",
};

const parseJson = async (response) => {
  if (response.status === 401) {
    window.location.href = "/admin/login";
    throw new Error("Session expired");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "Request failed");
  }
  return payload;
};

const updateExportLink = () => {
  const currency = dashboardCurrencySelect.value;
  exportCsvLink.href = currency ? `/api/admin/export.csv?currency=${encodeURIComponent(currency)}` : "/api/admin/export.csv";
  exportJsonLink.href = currency
    ? `/api/admin/export.json?currency=${encodeURIComponent(currency)}`
    : "/api/admin/export.json";
};

const resetFormToCreateMode = () => {
  form.reset();
  projectIdField.value = "";
  formTitle.textContent = "Add Project";
  saveProjectButton.textContent = "Save Project";
  cancelEditButton.hidden = true;
  milestonesContainer.innerHTML = "";
  createMilestoneItem();
};

const createMilestoneItem = (milestone = null) => {
  const content = milestoneTemplate.content.cloneNode(true);
  const item = content.querySelector(".milestone-item");
  const removeButton = content.querySelector(".remove-milestone");

  const titleField = content.querySelector("input[name='milestone_title']");
  const amountField = content.querySelector("input[name='milestone_amount']");
  const dueDateField = content.querySelector("input[name='milestone_due_date']");
  const paidField = content.querySelector("input[name='milestone_paid']");
  if (milestone) {
    titleField.value = milestone.title || "";
    amountField.value = milestone.amount ?? "";
    dueDateField.value = milestone.due_date || "";
    paidField.checked = Boolean(milestone.paid);
  }

  removeButton.addEventListener("click", () => item.remove());
  milestonesContainer.appendChild(content);
};

const collectMilestones = () => {
  const rows = Array.from(milestonesContainer.querySelectorAll(".milestone-item"));
  return rows
    .map((row) => ({
      title: row.querySelector("input[name='milestone_title']").value.trim(),
      amount: Number(row.querySelector("input[name='milestone_amount']").value),
      due_date: row.querySelector("input[name='milestone_due_date']").value,
      paid: row.querySelector("input[name='milestone_paid']").checked,
    }))
    .filter((milestone) => milestone.title && milestone.due_date && !Number.isNaN(milestone.amount));
};

const renderMilestoneRow = (milestone, currencyCode) => {
  const dueClass = milestone.paid ? "status-completed" : "status-upcoming";
  return `<span class="milestone-pill ${dueClass}">
      ${milestone.title} • ${formatCurrency(milestone.amount, currencyCode)} • ${milestone.due_date}
    </span>`;
};

const renderProjects = (projects, overview) => {
  if (!projects.length) {
    projectsTableBody.innerHTML = `
      <tr>
        <td colspan="7"><p class="empty-state">No projects found. Add one to start tracking.</p></td>
      </tr>
    `;
    return;
  }

  const totals = overview?.totals || {};
  const currencyCode = resolveDashboardCurrency(overview, projects);
  const totalContractValue =
    Number(totals.total_contract_value) || projects.reduce((sum, project) => sum + Number(project.total_price || 0), 0);
  const totalRevenueAmount =
    Number(totals.total_paid) || projects.reduce((sum, project) => sum + Number(project.paid_amount || 0), 0);
  const totalRemainingAmount =
    Number(totals.total_remaining) ||
    projects.reduce((sum, project) => sum + Number(project.metrics?.remaining_balance || 0), 0);

  const rowsHtml = projects
    .map((project) => {
      const metrics = project.metrics;
      const isCompleted = metrics.effective_status === "completed" || project.status === "completed";
      const displayDeadlineState = isCompleted ? "completed" : metrics.deadline_state;
      const displayDaysRemaining = isCompleted ? "Completed" : `${metrics.days_remaining} day(s) remaining`;
      const statusClass = `status-${metrics.effective_status}`;
      const deadlineClass = `status-${displayDeadlineState}`;
      const milestonesHtml = project.milestones.length
        ? project.milestones.map((milestone) => renderMilestoneRow(milestone, project.currency)).join("")
        : `<span class="empty-state">No milestones set.</span>`;

      return `
        <tr>
          <td>
            <p class="project-main">${project.client_name}</p>
            <p class="project-sub">ID #${project.id}</p>
          </td>
          <td>
            <p class="project-main">${project.project_name}</p>
            <p class="project-sub">Start: ${project.start_date}</p>
          </td>
          <td>
            <span class="status-badge ${statusClass}">
              ${statusLabelMap[metrics.effective_status] || metrics.effective_status}
            </span>
          </td>
          <td>
            <p class="project-main">${formatCurrency(project.paid_amount, project.currency)} / ${formatCurrency(project.total_price, project.currency)}</p>
            <p class="project-sub">Currency: ${project.currency} • Remaining: ${formatCurrency(metrics.remaining_balance, project.currency)}</p>
            <div class="progress-track"><div class="progress-fill" style="width:${metrics.payment_progress}%"></div></div>
            <p class="project-sub">${metrics.payment_progress}% paid</p>
          </td>
          <td>${milestonesHtml}</td>
          <td>
            <p class="project-main">${project.deadline}</p>
            <p class="project-sub">${displayDaysRemaining}</p>
            <span class="status-badge ${deadlineClass}">${displayDeadlineState.replace("_", " ")}</span>
          </td>
          <td>
            <div class="actions-cell">
              <button class="button ghost small edit-project" type="button" data-id="${project.id}">Edit</button>
              <button class="button danger small delete-project" type="button" data-id="${project.id}">Delete</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");

  const totalsRowHtml = `
    <tr class="totals-row">
      <td colspan="3">
        <p class="project-main">Portfolio Total</p>
        <p class="project-sub">${projects.length} project(s)</p>
      </td>
      <td>
        <p class="project-main">${formatCurrency(totalRevenueAmount, currencyCode)}</p>
        <p class="project-sub">Contracted: ${formatCurrency(totalContractValue, currencyCode)} • Remaining: ${formatCurrency(totalRemainingAmount, currencyCode)}</p>
      </td>
      <td colspan="3">
        <p class="project-sub">Revenues are counted from paid amounts only.</p>
      </td>
    </tr>
  `;

  projectsTableBody.innerHTML = rowsHtml + totalsRowHtml;
};

const renderOverview = (overview) => {
  const totals = overview.totals;
  const currencyCode = resolveDashboardCurrency(overview, cachedProjects);
  const totalRevenue =
    Number(totals.total_paid) || cachedProjects.reduce((sum, project) => sum + Number(project.paid_amount || 0), 0);
  const pendingBalance =
    Number(totals.total_remaining) ||
    cachedProjects.reduce((sum, project) => sum + Number(project.metrics?.remaining_balance || 0), 0);
  totalProjectsEl.textContent = totals.total_projects;
  activeProjectsEl.textContent = totals.active_projects;
  completedProjectsEl.textContent = totals.completed_projects;
  completedRevenueEl.textContent = formatCurrency(totalRevenue, currencyCode);
  pendingPaymentsEl.textContent = formatCurrency(pendingBalance, currencyCode);
  overduePaymentsEl.textContent = `${totals.overdue_payments_count} (${formatCurrency(totals.overdue_payments_amount, currencyCode)})`;
  portfolioProgressEl.textContent = `${totals.portfolio_payment_progress}%`;

  if (!overview.upcoming_deadlines.length) {
    deadlineListEl.innerHTML = "<li>No deadlines in the next 14 days.</li>";
    return;
  }

  deadlineListEl.innerHTML = overview.upcoming_deadlines
    .map(
      (item) => `
      <li>
        <strong>${item.project_name}</strong> (${item.client_name})<br/>
        Due ${item.deadline} • ${item.days_remaining} day(s) left
      </li>
    `
    )
    .join("");
};

const fetchProjects = async () => {
  const currency = dashboardCurrencySelect.value;
  const url = currency ? `/api/admin/projects?currency=${encodeURIComponent(currency)}` : "/api/admin/projects";
  const response = await fetch(url);
  const payload = await parseJson(response);
  return payload.projects || [];
};

const fetchOverview = async () => {
  const currency = dashboardCurrencySelect.value;
  const url = currency ? `/api/admin/overview?currency=${encodeURIComponent(currency)}` : "/api/admin/overview";
  const response = await fetch(url);
  return parseJson(response);
};

const refreshDashboard = async () => {
  updateExportLink();
  const [overview, projects] = await Promise.all([fetchOverview(), fetchProjects()]);
  cachedProjects = projects;
  renderOverview(overview);
  renderProjects(projects, overview);
};

const enterEditMode = (projectId) => {
  const project = cachedProjects.find((item) => Number(item.id) === Number(projectId));
  if (!project) return;
  projectIdField.value = String(project.id);
  form.client_name.value = project.client_name;
  form.project_name.value = project.project_name;
  form.currency.value = project.currency;
  form.total_price.value = String(project.total_price);
  form.paid_amount.value = String(project.paid_amount);
  form.start_date.value = project.start_date;
  form.deadline.value = project.deadline;
  form.status.value = project.status;
  form.notes.value = project.notes || "";

  milestonesContainer.innerHTML = "";
  if (project.milestones.length) {
    project.milestones.forEach((milestone) => createMilestoneItem(milestone));
  } else {
    createMilestoneItem();
  }

  formTitle.textContent = "Edit Project";
  saveProjectButton.textContent = "Update Project";
  cancelEditButton.hidden = false;
  form.scrollIntoView({ behavior: "smooth", block: "start" });
};

const deleteProject = async (projectId) => {
  const confirmed = window.confirm("Delete this project permanently?");
  if (!confirmed) return;
  try {
    const response = await fetch(`/api/admin/projects/${projectId}`, { method: "DELETE" });
    await parseJson(response);
    formFeedback.textContent = "Project deleted successfully.";
    formFeedback.className = "success";
    if (projectIdField.value && Number(projectIdField.value) === Number(projectId)) {
      resetFormToCreateMode();
    }
    await refreshDashboard();
  } catch (error) {
    formFeedback.textContent = error.message;
    formFeedback.className = "error";
  }
};

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formFeedback.className = "";
  formFeedback.textContent = "Saving project...";

  const payload = {
    client_name: form.client_name.value.trim(),
    project_name: form.project_name.value.trim(),
    currency: form.currency.value,
    total_price: Number(form.total_price.value),
    paid_amount: Number(form.paid_amount.value),
    start_date: form.start_date.value,
    deadline: form.deadline.value,
    status: form.status.value,
    notes: form.notes.value.trim(),
    milestones: collectMilestones(),
  };

  const editingProjectId = projectIdField.value;
  const isEditing = Boolean(editingProjectId);
  const endpoint = isEditing ? `/api/admin/projects/${editingProjectId}` : "/api/admin/projects";
  const method = isEditing ? "PUT" : "POST";

  try {
    const response = await fetch(endpoint, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await parseJson(response);
    formFeedback.textContent = isEditing ? "Project updated successfully." : "Project saved successfully.";
    formFeedback.className = "success";
    resetFormToCreateMode();
    await refreshDashboard();
  } catch (error) {
    formFeedback.textContent = error.message;
    formFeedback.className = "error";
  }
});

projectsTableBody.addEventListener("click", (event) => {
  const editButton = event.target.closest(".edit-project");
  const deleteButton = event.target.closest(".delete-project");
  if (editButton) {
    enterEditMode(Number(editButton.dataset.id));
  }
  if (deleteButton) {
    deleteProject(Number(deleteButton.dataset.id));
  }
});

cancelEditButton.addEventListener("click", () => {
  resetFormToCreateMode();
  formFeedback.textContent = "Edit cancelled.";
  formFeedback.className = "";
});

addMilestoneButton.addEventListener("click", () => createMilestoneItem());

dashboardCurrencySelect.addEventListener("change", async () => {
  try {
    await refreshDashboard();
  } catch (error) {
    formFeedback.textContent = error.message;
    formFeedback.className = "error";
  }
});

shareForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    client_email: shareForm.client_email.value.trim(),
    admin_email: shareForm.admin_email.value.trim(),
    currency: dashboardCurrencySelect.value || null,
  };
  shareResult.textContent = "Generating email draft...";
  try {
    const response = await fetch("/api/admin/share-report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await parseJson(response);
    shareResult.innerHTML = `
      <p>Draft ready for: <strong>${body.recipients}</strong></p>
      <a href="${body.mailto_link}">Open email draft</a>
    `;
  } catch (error) {
    shareResult.textContent = error.message;
  }
});

resetFormToCreateMode();
refreshDashboard().catch((error) => {
  formFeedback.textContent = error.message;
  formFeedback.className = "error";
});
