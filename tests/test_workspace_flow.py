from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from server.ecos_server.ecc.config import DEFAULT_PROJECTS_ROOT
from server.ecos_server.ecc.flow_spec import DEFAULT_FLOW_STEPS
from server.ecos_server.ecc.schemas.ecc import ECCRequest
from server.ecos_server.ecc.services.ecc import EccService


class WorkspaceFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.service = EccService()
        self.directory = self.root / "demo_workspace"
        self.create_req = ECCRequest(
            cmd="create_workspace",
            data={
                "directory": str(self.directory),
                "pdk": "ics55",
                "parameters": {
                    "Design": "demo",
                    "Top module": "top",
                    "Clock": "clk",
                },
                "origin_def": "",
                "origin_verilog": "",
                "filelist": "",
                "rtl_list": [],
            },
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_workspace_ecos_style_structure(self) -> None:
        response = self.service.create_workspace(self.create_req)
        self.assertEqual(response["response"], "success")
        project_dir = Path(response["data"]["directory"])

        self.assertTrue((project_dir / "log").is_dir())
        self.assertTrue((project_dir / "origin").is_dir())
        self.assertTrue((project_dir / "home").is_dir())
        self.assertTrue((project_dir / "log" / "server.log").exists())

        flow = json.loads((project_dir / "home" / "flow.json").read_text(encoding="utf-8"))
        actual = [(step["name"], step["tool"], step["state"]) for step in flow["steps"]]
        expected = [(name, tool, "Unstart") for name, tool in DEFAULT_FLOW_STEPS]
        self.assertEqual(actual, expected)

        for name, tool in DEFAULT_FLOW_STEPS:
            step_dir = project_dir / f"{name}_{tool}"
            self.assertTrue(step_dir.is_dir())
            for sub in ("config", "output", "data", "feature", "report", "log", "script", "analysis"):
                self.assertTrue((step_dir / sub).is_dir(), f"missing {sub} for {step_dir}")
            self.assertTrue((step_dir / "data" / "pl" / "density").is_dir())
            self.assertTrue((step_dir / "subflow.json").exists())
            self.assertTrue((step_dir / "checklist.json").exists())

    def test_rtl2gds_updates_step_state_and_outputs(self) -> None:
        self.service.create_workspace(self.create_req)
        response = self.service.rtl2gds(ECCRequest(cmd="rtl2gds", data={"rerun": False}))
        self.assertEqual(response["response"], "success")

        flow = json.loads((self.directory / "home" / "flow.json").read_text(encoding="utf-8"))
        for step in flow["steps"]:
            self.assertEqual(step["state"], "Success")
            self.assertRegex(step["runtime"], r"^\d{2}:\d{2}:\d{2}$")

        for name, tool in DEFAULT_FLOW_STEPS:
            step_dir = self.directory / f"{name}_{tool}"
            step_token = name.replace(" ", "_")
            self.assertTrue((step_dir / "log" / f"{step_token}.log").exists())
            self.assertTrue((step_dir / "output" / f"demo_{step_token}.v").exists())
            self.assertTrue((step_dir / "output" / f"demo_{step_token}.def.gz").exists())
            self.assertTrue((step_dir / "output" / f"demo_{step_token}.gds").exists())

    def test_run_single_step(self) -> None:
        self.service.create_workspace(self.create_req)
        response = self.service.run_step(ECCRequest(cmd="run_step", data={"step": "step1"}))
        self.assertEqual(response["response"], "success")
        self.assertEqual(response["data"]["state"], "Success")

    def test_create_workspace_uses_default_root_when_directory_empty(self) -> None:
        req = ECCRequest(
            cmd="create_workspace",
            data={"directory": "", "parameters": {"Design": "default_root_design"}},
        )
        response = self.service.create_workspace(req)
        self.assertEqual(response["response"], "success")
        self.assertEqual(
            response["data"]["directory"],
            str((DEFAULT_PROJECTS_ROOT / "default_root_design").resolve()),
        )


if __name__ == "__main__":
    unittest.main()
