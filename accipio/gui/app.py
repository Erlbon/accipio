"""Start Accipio: ``python -m accipio`` (or the exe)."""
from __future__ import annotations

import os
import sys

from redactor_common.gui.app_bootstrap import run_app

from accipio.core import aria2, settings
from accipio.core.version import APP_NAME


def main() -> int:
    folder = settings.config_dir()

    def make_window():
        from PyQt6.QtWidgets import QMessageBox

        from accipio.gui.main_window import MainWindow, open_services
        try:
            services = open_services(folder)
        except aria2.Aria2Error as error:
            QMessageBox.critical(None, APP_NAME, f"Accipio cannot start its download engine.\n\n{error}")
            raise SystemExit(1)
        return MainWindow(services)

    return run_app(APP_NAME, make_window, crash_log_path=os.path.join(folder, "crash.log"),
                   app_user_model_id="Erlbon.Accipio")


if __name__ == "__main__":
    sys.exit(main())
