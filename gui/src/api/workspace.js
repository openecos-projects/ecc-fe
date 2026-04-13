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
    data: { directory },
  });
}

export function rtl2gdsApi(payload = {}) {
  return postJson("/api/workspace/rtl2gds", {
    cmd: "rtl2gds",
    data: payload,
  });
}

export function runStepApi(payload) {
  return postJson("/api/workspace/run_step", {
    cmd: "run_step",
    data: payload,
  });
}

export function getHomePageApi() {
  return postJson("/api/workspace/get_home_page", {
    cmd: "home_page",
    data: {},
  });
}

export async function getExamplesApi() {
  const res = await fetch("/api/examples");
  const body = await res.json();
  return body.data || [];
}
