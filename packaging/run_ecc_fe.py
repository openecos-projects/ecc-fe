from __future__ import annotations

import multiprocessing

from fecompiler.cli.main import main


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
