import { useWorkspace } from "../composables/useWorkspace.js";
import { getExamplesApi } from "../api/workspace.js";

// ── DOM refs ──────────────────────────────────────────────────────────────────
const projectRootInput  = document.getElementById("project-root");
const projectNameInput  = document.getElementById("project-name");
const topModuleInput    = document.getElementById("top-module");
const clockInput        = document.getElementById("clock");
const filelistInput     = document.getElementById("filelist-path");
const btnExamples       = document.getElementById("btn-examples");
const examplesDropdown  = document.getElementById("examples-dropdown");
const output            = document.getElementById("output");
const createButton      = document.getElementById("btn-create");
const runButton         = document.getElementById("btn-run");
const stepsPanel        = document.getElementById("steps-panel");
const stepsList         = document.getElementById("steps-list");

const { newProject, runFlow, runStep } = useWorkspace();

// ── State ─────────────────────────────────────────────────────────────────────
let activeProjectDir = "";

// ── Init ──────────────────────────────────────────────────────────────────────
initDefaultProjectRoot();

// ── Helpers ───────────────────────────────────────────────────────────────────
function print(payload) {
  output.textContent = JSON.stringify(payload, null, 2);
}

function setLoading(btn, loading) {
  btn.disabled = loading;
  btn.style.opacity = loading ? "0.6" : "1";
}

// ── Examples dropdown ─────────────────────────────────────────────────────────
btnExamples.addEventListener("click", async (e) => {
  e.stopPropagation();
  if (!examplesDropdown.classList.contains("hidden")) {
    examplesDropdown.classList.add("hidden");
    return;
  }
  examplesDropdown.innerHTML = "<li>加载中...</li>";
  examplesDropdown.classList.remove("hidden");
  try {
    const files = await getExamplesApi();
    if (!files.length) {
      examplesDropdown.innerHTML = "<li style='color:#999'>无示例文件</li>";
      return;
    }
    examplesDropdown.innerHTML = "";
    files.forEach(({ name, path }) => {
      const li = document.createElement("li");
      li.textContent = name;
      li.title = path;
      li.addEventListener("click", () => {
        filelistInput.value = path;
        examplesDropdown.classList.add("hidden");
      });
      examplesDropdown.appendChild(li);
    });
  } catch {
    examplesDropdown.innerHTML = "<li style='color:#c00'>获取失败</li>";
  }
});

document.addEventListener("click", () => examplesDropdown.classList.add("hidden"));

// ── Step cards ────────────────────────────────────────────────────────────────
function renderSteps(stepWorkspaces) {
  stepsPanel.classList.remove("hidden");
  stepsList.innerHTML = "";
  stepWorkspaces.forEach(({ step, tool }) => {
    const card = document.createElement("div");
    card.className = "step-card";
    card.id = `step-card-${step}`;
    card.innerHTML = `
      <div class="step-name">${step}</div>
      <div class="step-tool">tool: ${tool}</div>
      <div class="step-state" id="state-${step}">Unstart</div>
      <button class="ghost small" data-step="${step}">▶ Run</button>
    `;
    card.querySelector("button").addEventListener("click", () =>
      execute(() => handleRunStep(step))
    );
    stepsList.appendChild(card);
  });
}

function setStepState(stepName, state) {
  const el = document.getElementById(`state-${stepName}`);
  if (!el) return;
  el.textContent = state;
  el.className = "step-state";
  if (state === "Success")    el.classList.add("success");
  if (state === "Ongoing")    el.classList.add("ongoing");
  if (["Incomplete", "Invalid", "Failed"].includes(state)) el.classList.add("failed");
}

// ── Handlers ──────────────────────────────────────────────────────────────────
async function handleWizardCreate() {
  if (!filelistInput.value.trim()) {
    throw new Error("请先指定 filelist.f 路径。");
  }
  setLoading(createButton, true);
  try {
    const config = {
      projectRoot:  projectRootInput.value.trim(),
      projectName:  projectNameInput.value.trim(),
      design:       projectNameInput.value.trim(),
      topModule:    topModuleInput.value.trim() || "top",
      clock:        clockInput.value.trim() || "clk",
      filelist:     filelistInput.value.trim(),
    };
    const result = await newProject(config);
    activeProjectDir = result.data.directory;
    renderSteps(result.data.step_workspaces || []);
    print({
      action:         "create_workspace",
      response:       result.response,
      projectDir:     result.data.directory,
      workspaceId:    result.data.workspace_id,
      stepWorkspaces: result.data.step_workspaces,
      message:        result.message,
    });
  } finally {
    setLoading(createButton, false);
  }
}

async function handleRunFlow() {
  if (!activeProjectDir) throw new Error("请先新建项目。");
  setLoading(runButton, true);
  try {
    const result = await runFlow(activeProjectDir, { rerun: false });
    // update all step states from reports
    (result.data.reports || []).forEach(({ step, state }) => setStepState(step, state));
    print({
      action:   "rtl2gds",
      response: result.response,
      reports:  result.data.reports,
      message:  result.message,
    });
  } finally {
    setLoading(runButton, false);
  }
}

async function handleRunStep(stepName) {
  if (!activeProjectDir) throw new Error("请先新建项目。");
  setStepState(stepName, "Ongoing");
  const result = await runStep(activeProjectDir, { step: stepName, rerun: true });
  const state = result.data.state || (result.response === "success" ? "Success" : "Incomplete");
  setStepState(stepName, state);
  print({
    action:   "run_step",
    step:     stepName,
    response: result.response,
    state:    result.data.state,
    message:  result.message,
  });
}

// ── Event bindings ────────────────────────────────────────────────────────────
createButton.addEventListener("click", () => execute(handleWizardCreate));
runButton.addEventListener("click",    () => execute(handleRunFlow));

async function execute(fn) {
  try {
    await fn();
  } catch (error) {
    print({ error: String(error.message || error) });
  }
}

// ── Default project root ──────────────────────────────────────────────────────
async function initDefaultProjectRoot() {
  try {
    const res  = await fetch("/api/config");
    const body = await res.json();
    if (res.ok && body.ok && body.data?.default_project_root) {
      projectRootInput.value = body.data.default_project_root;
    }
  } catch {
    // Keep manual input when server config is unavailable.
  }
}
