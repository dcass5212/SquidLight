import logging

import rgbapp
from rgbapp import paths
from rgbapp.logging_setup import setup_logging


def test_package_imports():
    assert rgbapp.__version__


def test_data_dir_is_under_appdata_not_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert paths.data_dir() == tmp_path / "RGBApp"
    assert paths.log_dir().is_dir()


def test_logging_writes_file(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    root = logging.getLogger()
    monkeypatch.setattr(root, "_rgbapp_configured", False, raising=False)
    before = list(root.handlers)
    try:
        setup_logging(console=False)
        logging.getLogger("rgbapp.test").info("hello")
        for h in root.handlers:
            h.flush()
        assert "hello" in (tmp_path / "RGBApp" / "logs" / "app.log").read_text(encoding="utf-8")
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()
