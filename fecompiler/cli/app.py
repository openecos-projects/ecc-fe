from __future__ import annotations

import json
from typing import Annotated

import typer

from fecompiler.cli import commands
from fecompiler.cli.core.inputs import (
    CatalogListInput,
    CatalogShowInput,
    CatalogValidateInput,
    CheckInput,
    CommandInput,
    ConfigInput,
    DoctorInput,
    InitInput,
    LogInput,
    ParamDiffInput,
    ParamListInput,
    ParamSetInput,
    ParamShowInput,
    ParamUnsetInput,
    ReportFilesInput,
    ReportQorInput,
    ResourceEnvInput,
    ResourceInstallInput,
    ResourceListInput,
    RunInput,
    StatusInput,
    output_mode,
)
from fecompiler.cli.core.invocation import execute_command
from fecompiler.cli.core.options import (
    JsonlOption,
    JsonOption,
    PlainOption,
    ProjectOption,
    RunIdOption,
    WorkspaceOption,
)
from fecompiler.cli.core.version_info import (
    root_version_line,
    version_payload,
    version_text,
)
from fecompiler.cli.handlers import catalog as catalog_handlers
from fecompiler.cli.handlers import param as param_handlers
from fecompiler.cli.handlers import report as report_handlers


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(root_version_line())
        raise typer.Exit()


def build_app() -> typer.Typer:
    app = typer.Typer(
        name="ecc-fe",
        help="ECC frontend project, flow, diagnostics, and resource CLI.",
        no_args_is_help=True,
        add_completion=False,
        pretty_exceptions_show_locals=False,
    )
    resource_app = typer.Typer(
        name="resource",
        help="Install and inspect ECC-FE runtime resources.",
        no_args_is_help=True,
        add_completion=False,
    )
    param_app = typer.Typer(
        name="param",
        help="Inspect and edit typed frontend parameter overrides.",
        no_args_is_help=True,
        add_completion=False,
    )
    catalog_app = typer.Typer(
        name="catalog",
        help="Inspect and validate frontend catalog entries.",
        no_args_is_help=True,
        add_completion=False,
    )
    report_app = typer.Typer(
        name="report",
        help="Inspect frontend QoR summaries and report files.",
        no_args_is_help=True,
        add_completion=False,
    )

    @app.callback()
    def root(
        version: Annotated[
            bool,
            typer.Option(
                "--version",
                callback=_version_callback,
                is_eager=True,
                help="Show the ECC-FE version and exit",
            ),
        ] = False,
    ) -> None:
        pass

    @app.command("version")
    def version_command(
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        mode = output_mode(json_output=json_output, jsonl=jsonl, plain=plain)
        payload = version_payload()
        if mode.value == "text":
            typer.echo(version_text(payload))
            return
        if mode.value == "json":
            typer.echo(json.dumps(payload, ensure_ascii=False))
            return
        from fecompiler.cli.core.types import CommandResult
        from fecompiler.cli.rendering.render import render_result

        render_result(
            "version", CommandResult.ok([{"kind": "version", **payload}]), mode
        )

    @app.command("init")
    def init_command(
        name: Annotated[
            str | None, typer.Argument(help="Project directory to create")
        ] = None,
        workspace: WorkspaceOption = None,
        design: Annotated[str, typer.Option("--design", help="Design name")] = "",
        top: Annotated[str, typer.Option("--top", help="Top module name")] = "top",
        rtl: Annotated[
            str | None, typer.Option("--rtl", help="Custom CPU RTL source")
        ] = None,
        cpu_filelist: Annotated[str | None, typer.Option("--cpu-filelist")] = None,
        soc_filelist: Annotated[str | None, typer.Option("--soc-filelist")] = None,
        core_id: Annotated[
            str | None,
            typer.Option("--core-id", help="CPU catalog id (default: picorv32)"),
        ] = None,
        soc_harness_id: Annotated[str | None, typer.Option("--soc-harness-id")] = None,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "init",
            InitInput(
                workspace=workspace,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                name=name,
                design=design,
                top=top,
                rtl=rtl,
                cpu_filelist=cpu_filelist,
                soc_filelist=soc_filelist,
                core_id=core_id,
                soc_harness_id=soc_harness_id,
            ),
            commands.init,
        )

    @app.command("run")
    def run_command(
        workspace: WorkspaceOption = None,
        project: ProjectOption = None,
        run_id: RunIdOption = None,
        step: Annotated[
            str | None, typer.Option("--step", help="Run one frontend step")
        ] = None,
        rerun: Annotated[
            bool, typer.Option("--rerun", help="Run completed work again")
        ] = False,
        overwrite: Annotated[
            bool, typer.Option("--overwrite", help="Replace an existing project run")
        ] = False,
        resume: Annotated[
            bool,
            typer.Option("--resume", help="Continue at the first unfinished step"),
        ] = False,
        from_step: Annotated[
            str | None,
            typer.Option("--from", help="Re-execute a step and its suffix"),
        ] = None,
        only: Annotated[
            str | None, typer.Option("--only", help="Run exactly one step")
        ] = None,
        force: Annotated[
            bool,
            typer.Option("--force", help="Re-execute a successful --only step"),
        ] = False,
        param_set: Annotated[
            list[str] | None,
            typer.Option(
                "--set",
                help="Set a run-local parameter (repeatable: --set key=value)",
            ),
        ] = None,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "run",
            RunInput(
                workspace=workspace,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                project=project,
                run_id=run_id,
                step=step,
                rerun=rerun,
                overwrite=overwrite,
                resume=resume,
                from_step=from_step,
                only=only,
                force=force,
                param_set=tuple(param_set or ()),
            ),
            commands.run_flow,
        )

    @app.command("check")
    def check_command(
        workspace: WorkspaceOption = None,
        project: ProjectOption = None,
        run_id: RunIdOption = None,
        step: Annotated[
            str | None, typer.Option("--step", help="Check one flow step")
        ] = None,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "check",
            CheckInput(
                workspace=workspace,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                project=project,
                run_id=run_id,
                step=step,
            ),
            commands.check,
        )

    @app.command("doctor")
    def doctor_command(
        workspace: WorkspaceOption = None,
        project: ProjectOption = None,
        run_id: RunIdOption = None,
        step: Annotated[
            str | None, typer.Option("--step", help="Check one flow step")
        ] = None,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "doctor",
            DoctorInput(
                workspace=workspace,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                project=project,
                run_id=run_id,
                step=step,
            ),
            commands.doctor,
        )

    @app.command("status")
    def status_command(
        workspace: WorkspaceOption = None,
        project: ProjectOption = None,
        run_id: RunIdOption = None,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "status",
            StatusInput(
                workspace=workspace,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                project=project,
                run_id=run_id,
            ),
            commands.status,
        )

    @app.command("log")
    def log_command(
        step_argument: Annotated[
            str | None, typer.Argument(help="Frontend step log")
        ] = None,
        workspace: WorkspaceOption = None,
        project: ProjectOption = None,
        run_id: RunIdOption = None,
        step_option: Annotated[
            str | None, typer.Option("--step", help="Frontend step log")
        ] = None,
        lines: Annotated[int, typer.Option("--lines", min=1, max=10000)] = 80,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        if step_argument is not None and step_option is not None:
            raise typer.BadParameter("pass the step once, positionally or with --step")
        step = step_argument if step_argument is not None else step_option
        execute_command(
            "log",
            LogInput(
                workspace=workspace,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                project=project,
                run_id=run_id,
                step=step,
                lines=lines,
            ),
            commands.log,
        )

    @app.command("config")
    def config_command(
        step_argument: Annotated[
            str | None, typer.Argument(help="Frontend step config")
        ] = None,
        workspace: WorkspaceOption = None,
        project: ProjectOption = None,
        run_id: RunIdOption = None,
        step_option: Annotated[
            str | None, typer.Option("--step", help="Frontend step config")
        ] = None,
        resolved: Annotated[
            bool, typer.Option("--resolved", help="Include derived workspace values")
        ] = False,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        if step_argument is not None and step_option is not None:
            raise typer.BadParameter("pass the step once, positionally or with --step")
        step = step_argument if step_argument is not None else step_option
        execute_command(
            "config",
            ConfigInput(
                workspace=workspace,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                project=project,
                run_id=run_id,
                step=step,
                resolved=resolved,
            ),
            commands.config,
        )

    @resource_app.command("list")
    def resource_list_command(
        installed: Annotated[
            bool, typer.Option("--installed", help="Only show installed resources")
        ] = False,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "resource.list",
            ResourceListInput(
                workspace=None,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                installed_only=installed,
            ),
            commands.resource_list,
        )

    @resource_app.command("status")
    def resource_status_command(
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "resource.status",
            StatusInput(
                workspace=None,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
            ),
            commands.resource_status,
        )

    def install_or_update(
        command_name: str,
        resource_id: str | None,
        required: bool,
        version: str | None,
        json_output: bool,
        jsonl: bool,
        plain: bool,
    ) -> None:
        execute_command(
            command_name,
            ResourceInstallInput(
                workspace=None,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                resource_id=resource_id,
                version=version,
                required=required,
            ),
            commands.resource_install,
        )

    @resource_app.command("install")
    def resource_install_command(
        resource_id: Annotated[
            str | None, typer.Argument(help="Resource id, for example slang")
        ] = None,
        required: Annotated[
            bool, typer.Option("--required", help="Install all ECC-FE dependencies")
        ] = False,
        version: Annotated[
            str | None, typer.Option("--version", help="Install a specific version")
        ] = None,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        install_or_update(
            "resource.install",
            resource_id,
            required,
            version,
            json_output,
            jsonl,
            plain,
        )

    @resource_app.command("update")
    def resource_update_command(
        resource_id: Annotated[
            str | None, typer.Argument(help="Resource id, for example slang")
        ] = None,
        required: Annotated[
            bool, typer.Option("--required", help="Update all ECC-FE dependencies")
        ] = False,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        install_or_update(
            "resource.update", resource_id, required, None, json_output, jsonl, plain
        )

    @resource_app.command("env")
    def resource_env_command(
        shell: Annotated[
            str, typer.Option("--shell", help="sh, bash, zsh, or fish")
        ] = "sh",
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "resource.env",
            ResourceEnvInput(
                workspace=None,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                shell=shell,
            ),
            commands.resource_env,
        )

    @param_app.command("list")
    def param_list_command(
        workspace: WorkspaceOption = None,
        project: ProjectOption = None,
        step: Annotated[str | None, typer.Option("--step")] = None,
        all_parameters: Annotated[
            bool, typer.Option("--all", help="Include parameters at default values")
        ] = False,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "param.list",
            ParamListInput(
                workspace=workspace,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                project=project,
                step=step,
                all_parameters=all_parameters,
            ),
            param_handlers.list_parameters,
        )

    @param_app.command("show")
    def param_show_command(
        key: Annotated[str, typer.Argument(help="Parameter name")],
        workspace: WorkspaceOption = None,
        project: ProjectOption = None,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "param.show",
            ParamShowInput(
                workspace=workspace,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                project=project,
                key=key,
            ),
            param_handlers.show_parameter,
        )

    @param_app.command("set")
    def param_set_command(
        key: Annotated[str, typer.Argument(help="Parameter name")],
        value: Annotated[
            str | None,
            typer.Argument(help="Typed parameter value"),
        ] = None,
        value_option: Annotated[
            str | None,
            typer.Option(
                "--value",
                help="Value form for strings such as -O3: --value=-O3",
            ),
        ] = None,
        workspace: WorkspaceOption = None,
        project: ProjectOption = None,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        if value is not None and value_option is not None:
            raise typer.BadParameter(
                "pass the value once, positionally or with --value"
            )
        selected_value = value_option if value_option is not None else value
        if selected_value is None:
            raise typer.BadParameter("missing parameter value")
        execute_command(
            "param.set",
            ParamSetInput(
                workspace=workspace,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                project=project,
                key=key,
                value=selected_value,
            ),
            param_handlers.set_parameter,
        )

    @param_app.command("unset")
    def param_unset_command(
        key: Annotated[str, typer.Argument(help="Parameter name")],
        workspace: WorkspaceOption = None,
        project: ProjectOption = None,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "param.unset",
            ParamUnsetInput(
                workspace=workspace,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                project=project,
                key=key,
            ),
            param_handlers.unset_parameter,
        )

    @param_app.command("diff")
    def param_diff_command(
        workspace: WorkspaceOption = None,
        project: ProjectOption = None,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "param.diff",
            ParamDiffInput(
                workspace=workspace,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                project=project,
            ),
            param_handlers.diff_parameters,
        )

    @catalog_app.command("list")
    def catalog_list_command(
        kind: Annotated[
            str | None,
            typer.Option("--kind", help="core, soc, toolchain, or test-suite"),
        ] = None,
        status: Annotated[str | None, typer.Option("--status")] = None,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "catalog.list",
            CatalogListInput(
                workspace=None,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                kind=kind,
                status=status,
            ),
            catalog_handlers.list_catalog,
        )

    @catalog_app.command("show")
    def catalog_show_command(
        entry_id: Annotated[str, typer.Argument(help="Catalog entry id")],
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "catalog.show",
            CatalogShowInput(
                workspace=None,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                entry_id=entry_id,
            ),
            catalog_handlers.show_catalog,
        )

    @catalog_app.command("check")
    def catalog_check_command(
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "catalog.check",
            CommandInput(
                workspace=None,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
            ),
            catalog_handlers.check_catalog,
        )

    @catalog_app.command("validate")
    def catalog_validate_command(
        core_id: Annotated[str | None, typer.Option("--core-id")] = None,
        soc_harness_id: Annotated[str | None, typer.Option("--soc-harness-id")] = None,
        toolchain_id: Annotated[str | None, typer.Option("--toolchain-id")] = None,
        test_suite_id: Annotated[str | None, typer.Option("--test-suite-id")] = None,
        cpu_filelist: Annotated[str | None, typer.Option("--cpu-filelist")] = None,
        cpu_top_module: Annotated[str | None, typer.Option("--cpu-top-module")] = None,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "catalog.validate",
            CatalogValidateInput(
                workspace=None,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                core_id=core_id,
                soc_harness_id=soc_harness_id,
                toolchain_id=toolchain_id,
                test_suite_id=test_suite_id,
                cpu_filelist=cpu_filelist,
                cpu_top_module=cpu_top_module,
            ),
            catalog_handlers.validate_catalog,
        )

    @report_app.command("qor")
    def report_qor_command(
        workspace: WorkspaceOption = None,
        project: ProjectOption = None,
        run_id: RunIdOption = None,
        output_path: Annotated[
            str | None, typer.Option("--output", "-o", help="Write the JSON report")
        ] = None,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "report.qor",
            ReportQorInput(
                workspace=workspace,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                project=project,
                run_id=run_id,
                output_path=output_path,
            ),
            report_handlers.qor,
        )

    @report_app.command("files")
    def report_files_command(
        workspace: WorkspaceOption = None,
        project: ProjectOption = None,
        run_id: RunIdOption = None,
        step: Annotated[str | None, typer.Option("--step")] = None,
        json_output: JsonOption = False,
        jsonl: JsonlOption = False,
        plain: PlainOption = False,
    ) -> None:
        execute_command(
            "report.files",
            ReportFilesInput(
                workspace=workspace,
                output_mode=output_mode(
                    json_output=json_output, jsonl=jsonl, plain=plain
                ),
                project=project,
                run_id=run_id,
                step=step,
            ),
            report_handlers.files,
        )

    @app.command(
        "workspace",
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    )
    def workspace_compatibility(ctx: typer.Context) -> None:
        """Use the ECOS Studio-compatible workspace command group."""
        raise typer.Exit(code=2)

    @app.command(
        "rpc",
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    )
    def rpc_compatibility(ctx: typer.Context) -> None:
        """Start or invoke the JSON-RPC compatibility transport."""
        raise typer.Exit(code=2)

    app.add_typer(resource_app, name="resource")
    app.add_typer(param_app, name="param")
    app.add_typer(catalog_app, name="catalog")
    app.add_typer(report_app, name="report")
    return app


app = build_app()
