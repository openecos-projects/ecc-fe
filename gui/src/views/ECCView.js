import { useWorkspace } from "../composables/useWorkspace.js";

const projectRootInput = document.getElementById("project-root");
const projectNameInput = document.getElementById("project-name");
const topModuleInput = document.getElementById("top-module");
const clockInput = document.getElementById("clock");
const output = document.getElementById("output");
const createButton = document.getElementById("btn-create");
const runButton = document.getElementById("btn-run");

const { newProject, runFlow } = useWorkspace();
let activeProjectDir = "";

initDefaultProjectRoot();

function print(payload) {
  output.textContent = JSON.stringify(payload, null, 2);
}

async function handleWizardCreate() {
  const config = {
    projectRoot: projectRootInput.value.trim(),
    projectName: projectNameInput.value.trim(),
    design: projectNameInput.value.trim(),
    topModule: topModuleInput.value.trim() || "top",
    clock: clockInput.value.trim() || "clk",
  };
  const result = await newProject(config);
  activeProjectDir = result.data.directory;
  print({
    action: "create_workspace",
    response: result.response,
    projectDir: result.data.directory,
    workspaceId: result.data.workspace_id,
    stepWorkspaces: result.data.step_workspaces,
    message: result.message,
  });
}

async function handleRunFlow() {
  if (!activeProjectDir) {
    throw new Error("请先新建项目。");
  }
  const result = await runFlow({ rerun: false });
  print({
    action: "rtl2gds",
    response: result.response,
    reports: result.data.reports,
    message: result.message,
  });
}

createButton.addEventListener("click", () => execute(handleWizardCreate));
runButton.addEventListener("click", () => execute(handleRunFlow));

async function execute(fn) {
  try {
    await fn();
  } catch (error) {
    print({
      error: String(error.message || error),
    });
  }
}

async function initDefaultProjectRoot() {
  try {
    const response = await fetch("/api/config");
    const body = await response.json();
    if (response.ok && body.ok && body.data?.default_project_root) {
      projectRootInput.value = body.data.default_project_root;
    }
  } catch (_error) {
    // Keep manual input when server config is unavailable.
  }
}
