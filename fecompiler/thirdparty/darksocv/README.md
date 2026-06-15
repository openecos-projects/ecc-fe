# DarkSoCV Catalog Entry

DarkSoCV is the SoC that ships with DarkRISCV.  It is listed as an ECOS
candidate so users can see the direction of the catalog, but it is not wired to
the ECOS simulator contract yet.

Planned work:

- add or reuse a simulator-facing `ecos_sim_top` wrapper,
- connect the SoC or its CPU socket to `ysyx-axi-cpu-socket-v1`,
- decide whether to use the DarkRISCV native firmware flow or ECOS CPU tests.
