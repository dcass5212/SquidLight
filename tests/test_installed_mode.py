"""Installed mode: OpenRGBManager drives a scheduled task (faked), the task XML, autostart."""

import sys

import pytest

from rgbapp.engine.openrgb_manager import ManagerState, OpenRGBManager, server_args
from rgbapp.system.openrgb_task import TaskAction, TaskError, build_action, build_xml, parse_action


class FakeTask:
    def __init__(self, env):
        self.env = env
        self.action = None  # registered TaskAction, None = missing
        self.created = []
        self.runs = 0
        self.ends = 0
        self.decline = False

    def query(self):
        return self.action

    def create_elevated(self, action):
        if self.decline:
            raise TaskError("declined", cancelled=True)
        self.created.append(action)
        self.action = action

    def run(self):
        self.runs += 1
        if self.env.opens_after_run:
            self.env.port_open = True
            self.env.ours = True

    def end(self):
        self.ends += 1
        self.env.port_open = False
        self.env.ours = False


class Env:
    def __init__(self, exe):
        self.exe = exe
        self.now = 0.0
        self.port_open = False
        self.ours = False
        self.opens_after_run = True
        self.task = FakeTask(self)

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s

    def probe(self, host, port):
        assert host == "127.0.0.1"
        return self.port_open

    def manager(self, tmp_path, **kw):
        def no_spawn(args, cwd):
            raise AssertionError("installed mode must never spawn OpenRGB directly")

        return OpenRGBManager(
            exe_path=self.exe, config_dir=tmp_path / "cfg", launcher=no_spawn, port_probe=self.probe,
            clock=self.clock, sleep=self.sleep, mode="installed", task=self.task,
            is_ours=lambda exe: self.ours, **kw,
        )


@pytest.fixture
def env(tmp_path):
    exe = tmp_path / "OpenRGB.exe"
    exe.write_bytes(b"")
    return Env(exe)


def test_missing_task_is_created_once_then_run(env, tmp_path):
    m = env.manager(tmp_path)
    assert m.start() is ManagerState.RUNNING and not m.external
    assert len(env.task.created) == 1 and env.task.runs == 1
    action = env.task.created[0]
    assert action.command == str(env.exe)
    assert "--server-host 127.0.0.1" in action.arguments and "--startminimized" in action.arguments
    m.stop()
    assert env.task.ends == 1 and m.state is ManagerState.STOPPED
    # next time the task is there and matches: no UAC prompt
    m.start()
    assert len(env.task.created) == 1 and env.task.runs == 2


def test_a_task_for_another_port_is_replaced(env, tmp_path):
    env.task.action = build_action(server_args(env.exe, 6742, tmp_path / "cfg"))
    m = env.manager(tmp_path, port=6800)
    assert m.start() is ManagerState.RUNNING
    assert len(env.task.created) == 1 and "6800" in env.task.created[0].arguments


def test_declined_uac_fails_and_is_not_asked_again_until_restart(env, tmp_path):
    env.task.decline = True
    m = env.manager(tmp_path)
    assert m.start() is ManagerState.FAILED and "administrator" in m.reason
    env.task.decline = False
    assert m.start() is ManagerState.FAILED  # host retries must not re-prompt
    assert env.task.created == []
    m.configure(env.exe, 6742)  # what Restart OpenRGB in Settings does
    assert m.start() is ManagerState.RUNNING and len(env.task.created) == 1


def test_our_server_already_running_from_logon_is_adopted_and_stopped_on_quit(env, tmp_path):
    env.port_open, env.ours = True, True
    m = env.manager(tmp_path)
    assert m.start() is ManagerState.RUNNING
    assert env.task.runs == 0 and env.task.created == []
    m.stop()
    assert env.task.ends == 1


def test_someone_elses_server_is_external_and_left_alone(env, tmp_path):
    env.port_open, env.ours = True, False
    m = env.manager(tmp_path)
    assert m.start() is ManagerState.EXTERNAL
    m.stop()
    assert env.task.ends == 0 and env.port_open


def test_supervisor_restarts_the_task_after_two_missed_checks(env, tmp_path):
    m = env.manager(tmp_path)
    m.start()
    env.port_open = False
    m.supervise_once()
    assert m.state is ManagerState.RUNNING  # one miss is tolerated
    m.supervise_once()
    assert m.state is ManagerState.STARTING
    env.now += 30
    m.supervise_once()
    assert m.state is ManagerState.RUNNING and env.task.runs == 2


def test_port_that_never_opens_fails_and_ends_the_task(env, tmp_path):
    env.opens_after_run = False
    m = env.manager(tmp_path, start_timeout=5.0)
    assert m.start() is ManagerState.FAILED and "5s" in m.reason
    assert env.task.ends == 1


def test_missing_bundled_exe_is_permanent(tmp_path, monkeypatch):
    import rgbapp.engine.openrgb_manager as om

    monkeypatch.setattr(om, "locate_exe", lambda explicit=None: None)
    m = Env(tmp_path / "missing" / "OpenRGB.exe").manager(tmp_path)
    assert m.start() is ManagerState.FAILED and "Reinstall" in m.reason


# ---------------------------------------------------------------- task XML


def test_task_xml_round_trips_the_action_and_has_no_time_limit(tmp_path):
    exe = tmp_path / "Squid Light" / "openrgb" / "OpenRGB.exe"
    action = build_action(server_args(exe, 6742, tmp_path / "cfg dir"))
    xml = build_xml(action, user="PC\\me")
    assert "<RunLevel>HighestAvailable</RunLevel>" in xml
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml  # the default would kill it after 72h
    assert "<LogonTrigger>" in xml and "PC\\me" in xml
    parsed = parse_action(xml)
    assert parsed is not None and parsed.same_as(action)
    assert '"' in parsed.arguments  # the config path with a space is quoted


def test_same_as_ignores_quotes_case_and_spacing():
    a = TaskAction(r'"C:\Apps\OpenRGB.exe"', "--server  --server-port 6742")
    b = TaskAction(r"c:\apps\openrgb.exe", "--server --server-port 6742")
    assert a.same_as(b)
    assert not a.same_as(TaskAction(r"c:\apps\openrgb.exe", "--server --server-port 6800"))


def test_parse_action_handles_schtasks_output_and_garbage():
    out = '<?xml version="1.0" encoding="UTF-16"?>\r\r\n' + build_xml(TaskAction("C:\\x.exe", "--a")).split("?>", 1)[1]
    assert parse_action(out) == TaskAction("C:\\x.exe", "--a")
    assert parse_action("not xml") is None


# ---------------------------------------------------------------- autostart


@pytest.mark.skipif(sys.platform != "win32", reason="registry")
def test_autostart_writes_and_removes_the_run_value(monkeypatch):
    import winreg

    from rgbapp.system import autostart

    monkeypatch.setattr(autostart, "RUN_KEY", r"Software\SquidLightTests\Run")
    try:
        assert not autostart.is_enabled()
        autostart.enable(minimized=True)
        cmd = autostart.current()
        assert cmd and cmd.endswith("--minimized") and "-m rgbapp" in cmd
        autostart.enable(minimized=False)
        assert not autostart.current().endswith("--minimized")
        autostart.disable()
        autostart.disable()  # twice is fine
        assert not autostart.is_enabled()
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\SquidLightTests\Run")
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\SquidLightTests")
        except OSError:
            pass
