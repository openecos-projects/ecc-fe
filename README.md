# ecc-fe

`ecc-fe` is a lightweight workflow skeleton project aligned with the `ecos` style.
It provides:

- a minimal GUI (`gui/`) to create and run a workspace flow
- a Python backend (`server/ecos_server/`) with `create_workspace`, `rtl2gds`, and `run_step` APIs
- a step-based workspace layout (`step1_ecc` ... `step6_ecc`) under `workspace_projects/`

The current flow execution is mocked (placeholder outputs), so this repository is focused on structure and orchestration rather than real EDA execution.

## Repository Layout

```text
ecc-fe/
├── docs/                      # Detailed docs
├── gui/                       # Frontend UI
├── scripts/                   # Helper scripts
├── server/                    # Backend source
│   └── ecos_server/
└── tests/                     # Backend tests
```

## Quick Start

### 1) Run with Python

```bash
cd /home/luyoung/ecc-fe
python3 -m server.ecos_server.main
```

Open: `http://127.0.0.1:8080`

You can also set a custom port:

```bash
ECC_FE_SERVER_PORT=18080 python3 -m server.ecos_server.main
```

### 2) Run with Bazel

```bash
cd /home/luyoung/ecc-fe
bazel run //:server
```

Custom port:

```bash
bazel run //:server -- --port 18080
```

### 3) Run helper scripts

```bash
cd /home/luyoung/ecc-fe
./scripts/run_server.sh
./scripts/run_tests.sh
```

## Tests

```bash
cd /home/luyoung/ecc-fe
python3 -m unittest tests.test_workspace_flow -v
bazel test //:workspace_flow_test
```

## API Endpoints

- `POST /api/workspace/create_workspace`
- `POST /api/workspace/load_workspace`
- `POST /api/workspace/rtl2gds`
- `POST /api/workspace/run_step`
- `POST /api/workspace/get_home_page`
- `GET /api/workspace/health`

## Documentation

- Chinese detailed walkthrough: [`docs/README.zh-CN.md`](docs/README.zh-CN.md)
