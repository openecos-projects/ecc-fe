import tempfile
from pathlib import Path
from fecompiler.engine.flow import EngineFlow
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace

with tempfile.TemporaryDirectory() as tmpdir:
    tmp_path = Path(tmpdir)
    rtl = tmp_path / "chip_top.v"
    rtl.write_text("module chip_top(); endmodule\n", encoding="utf-8")
    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        origin_verilog=str(rtl),
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws"))
    engine = EngineFlow(workspace=ws)
    if not engine.has_init():
        engine.init_default_steps()
        engine.load()
    engine.create_step_workspaces()

    lint_ws_step = next(ws_step for ws_step in engine.workspace_steps if ws_step["name"] == "lint")
    print("lint ws_step:", lint_ws_step)
    handler = engine._get_handler("lint")
    print("handler:", handler)
    result = handler.run(lint_ws_step, engine.workspace)
    print("result:", result)
