import { createWorkspaceApi, loadWorkspaceApi, rtl2gdsApi, runStepApi } from "../api/workspace.js";

function buildBackendParameters(config) {
  const design = config.design || config.projectName || "New_Chip_Design";
  return {
    Design: design,
    "Top module": config.topModule || "top",
    Clock: config.clock || "clk",
    "Frequency max [MHz]": Number(config.frequencyMax || 100),
    PDK: config.pdk || "ics55",
    Core: {
      Utilitization: Number(config.coreUtilization || 0.5),
    },
    "Target density": Number(config.targetDensity || 0.6),
    "Max fanout": Number(config.maxFanout || 20),
    ...(config.parameters || {}),
  };
}

export function useWorkspace() {
  async function newProject(config) {
    const root = String(config.projectRoot || "").replace(/\/+$/, "");
    const name = String(config.projectName || "New_Chip_Design");
    const directory = config.directory || (root ? `${root}/${name}` : "");
    return createWorkspaceApi({
      directory,
      pdk: config.pdk || "ics55",
      pdk_root: config.pdkRoot || "",
      parameters: buildBackendParameters(config),
      origin_def: config.originDef || "",
      origin_verilog: config.originVerilog || "",
      filelist: config.filelist || "",
      rtl_list: config.rtlList || [],
    });
  }

  async function loadProject(directory) {
    return loadWorkspaceApi(directory);
  }

  async function runFlow(workspaceId, config = {}) {
    return rtl2gdsApi(workspaceId, { rerun: Boolean(config.rerun) });
  }

  async function runStep(workspaceId, config) {
    return runStepApi(workspaceId, {
      step: config.step,
      rerun: Boolean(config.rerun),
    });
  }

  return { newProject, loadProject, runFlow, runStep };
}
