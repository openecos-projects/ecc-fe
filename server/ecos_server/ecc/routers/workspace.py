"""Workspace command router."""

from __future__ import annotations

from typing import Any

from ..schemas.ecc import CMDEnum, parse_ecc_request
from ..services.ecc import EccService

ecc_serv = EccService()


def create_workspace(payload: dict[str, Any]) -> dict[str, Any]:
    req = parse_ecc_request(payload, expected=CMDEnum.create_workspace)
    return ecc_serv.create_workspace(req)


def load_workspace(payload: dict[str, Any]) -> dict[str, Any]:
    req = parse_ecc_request(payload, expected=CMDEnum.load_workspace)
    return ecc_serv.load_workspace(req)


def rtl2gds(payload: dict[str, Any]) -> dict[str, Any]:
    req = parse_ecc_request(payload, expected=CMDEnum.rtl2gds)
    return ecc_serv.rtl2gds(req)


def run_step(payload: dict[str, Any]) -> dict[str, Any]:
    req = parse_ecc_request(payload, expected=CMDEnum.run_step)
    return ecc_serv.run_step(req)


def get_home_page(payload: dict[str, Any]) -> dict[str, Any]:
    req = parse_ecc_request(payload, expected=CMDEnum.home_page)
    return ecc_serv.get_home_page(req)


def run_flow_compat(payload: dict[str, Any]) -> dict[str, Any]:
    req = parse_ecc_request(payload, expected=CMDEnum.rtl2gds)
    return ecc_serv.rtl2gds(req)
