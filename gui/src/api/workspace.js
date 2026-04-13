const JSON_HEADERS = { "Content-Type": "application/json" };

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error((body.message && body.message.join("; ")) || `Request failed: ${response.status}`);
  }
  if (body.response && body.response !== "success") {
    throw new Error((body.message && body.message.join("; ")) || "Command failed");
  }
  return body;
}

export function createWorkspaceApi(payload) {
  return postJson("/api/workspace/create_workspace", {
    cmd: "create_workspace",
    data: payload,
  });
}

export function loadWorkspaceApi(directory) {
  return postJson("/api/workspace/load_workspace", {
    cmd: "load_workspace",
    data: { directory, workspace_id: directory },
  });
}

export function rtl2gdsApi(workspaceId, payload = {}) {
  return postJson("/api/workspace/rtl2gds", {
    cmd: "rtl2gds",
    data: { ...payload, workspace_id: workspaceId },
  });
}

export function runStepApi(workspaceId, payload) {
  return postJson("/api/workspace/run_step", {
    cmd: "run_step",
    data: { ...payload, workspace_id: workspaceId },
  });
}

export function getHomePageApi(workspaceId) {
  return postJson("/api/workspace/get_home_page", {
    cmd: "home_page",
    data: { workspace_id: workspaceId },
  });
}

export async function getExamplesApi() {
  const res = await fetch("/api/examples");
  const body = await res.json();
  return body.data || [];
}
