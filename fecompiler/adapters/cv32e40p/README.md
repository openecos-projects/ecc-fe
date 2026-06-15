# CV32E40P CPU Adapter

CV32E40P is listed in the ECOS frontend catalog as an open-source CPU
candidate. This directory contains the experimental ECOS adapter used to wire
the upstream integer-core configuration into the shared frontend CPU socket.

Upstream repository: https://github.com/openhwgroup/cv32e40p

Current ECOS integration:

- Wrapper top: `ecos_cv32e40p_cpu_wrapper`
- Compatibility top: `ysyx_00000000`
- CPU socket: `ysyx-axi-cpu-socket-v1`
- Filelist: `filelist.cpu.f`
- Supported suites: `cpu-tests`, `smoke`
- Difftest: not supported

The first adapter revision keeps the upstream RTL untouched and uses
CV32E40P's integer-core path (`FPU=0`, `COREV_PULP=0`, `COREV_CLUSTER=0`).
It is intended for lightweight CPU test smoke runs before richer Core-V
features are enabled.
