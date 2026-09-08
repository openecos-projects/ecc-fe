from __future__ import annotations

from dataclasses import dataclass

from fecompiler.cli.core.types import OutputMode


@dataclass(frozen=True, slots=True)
class CommandInput:
    workspace: str | None
    output_mode: OutputMode
    project: str | None = None
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class CheckInput(CommandInput):
    step: str | None = None


@dataclass(frozen=True, slots=True)
class DoctorInput(CommandInput):
    step: str | None = None


@dataclass(frozen=True, slots=True)
class InitInput(CommandInput):
    name: str | None = None
    design: str = ""
    top: str = "top"
    rtl: str | None = None
    cpu_filelist: str | None = None
    soc_filelist: str | None = None
    core_id: str | None = None
    soc_harness_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunInput(CommandInput):
    step: str | None = None
    rerun: bool = False
    overwrite: bool = False
    resume: bool = False
    from_step: str | None = None
    only: str | None = None
    force: bool = False
    param_set: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StatusInput(CommandInput):
    pass


@dataclass(frozen=True, slots=True)
class LogInput(CommandInput):
    step: str | None = None
    lines: int = 80


@dataclass(frozen=True, slots=True)
class ConfigInput(CommandInput):
    step: str | None = None
    resolved: bool = False


@dataclass(frozen=True, slots=True)
class ResourceListInput(CommandInput):
    installed_only: bool = False


@dataclass(frozen=True, slots=True)
class ResourceInstallInput(CommandInput):
    resource_id: str | None = None
    version: str | None = None
    required: bool = False


@dataclass(frozen=True, slots=True)
class ResourceEnvInput(CommandInput):
    shell: str = "sh"


@dataclass(frozen=True, slots=True)
class ParamListInput(CommandInput):
    step: str | None = None
    all_parameters: bool = False


@dataclass(frozen=True, slots=True)
class ParamShowInput(CommandInput):
    key: str = ""


@dataclass(frozen=True, slots=True)
class ParamSetInput(CommandInput):
    key: str = ""
    value: str = ""


@dataclass(frozen=True, slots=True)
class ParamUnsetInput(CommandInput):
    key: str = ""


@dataclass(frozen=True, slots=True)
class ParamDiffInput(CommandInput):
    pass


@dataclass(frozen=True, slots=True)
class CatalogListInput(CommandInput):
    kind: str | None = None
    status: str | None = None


@dataclass(frozen=True, slots=True)
class CatalogShowInput(CommandInput):
    entry_id: str = ""


@dataclass(frozen=True, slots=True)
class CatalogValidateInput(CommandInput):
    core_id: str | None = None
    soc_harness_id: str | None = None
    toolchain_id: str | None = None
    test_suite_id: str | None = None
    cpu_filelist: str | None = None
    cpu_top_module: str | None = None


@dataclass(frozen=True, slots=True)
class ReportQorInput(CommandInput):
    output_path: str | None = None


@dataclass(frozen=True, slots=True)
class ReportFilesInput(CommandInput):
    step: str | None = None


def output_mode(*, json_output: bool, jsonl: bool, plain: bool) -> OutputMode:
    if jsonl:
        return OutputMode.JSONL
    if json_output:
        return OutputMode.JSON
    if plain:
        return OutputMode.PLAIN
    return OutputMode.TEXT
