"""OpenRGBManager with a fake process launcher, fake port probe and fake clock."""

import subprocess

import pytest

from rgbapp.engine.openrgb_manager import ManagerState, OpenRGBManager, locate_exe


class FakeProc:
    def __init__(self):
        self.code = None
        self.terminated = self.killed = False
        self.ignore_terminate = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        if not self.ignore_terminate:
            self.code = 0

    def kill(self):
        self.killed = True
        self.code = -9

    def wait(self, timeout=None):
        if self.code is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.code


class Env:
    """Controllable world: is the port open, what launching does, what time it is."""

    def __init__(self, exe):
        self.port_open = False
        self.opens_after_launch = True
        self.launch_error = None
        self.now = 0.0
        self.launches = []
        self.procs = []
        self.exe = exe

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s

    def probe(self, host, port):
        assert host == "127.0.0.1"
        return self.port_open

    def launcher(self, args, cwd):
        if self.launch_error:
            raise self.launch_error
        self.launches.append(args)
        proc = FakeProc()
        self.procs.append(proc)
        if self.opens_after_launch:
            self.port_open = True
        return proc

    def manager(self, tmp_path, **kw):
        return OpenRGBManager(
            exe_path=self.exe, config_dir=tmp_path / "cfg", launcher=self.launcher, port_probe=self.probe,
            clock=self.clock, sleep=self.sleep, **kw,
        )


@pytest.fixture
def env(tmp_path):
    exe = tmp_path / "OpenRGB.exe"
    exe.write_bytes(b"")
    return Env(exe)


def test_external_server_is_used_and_never_spawned_or_stopped(env, tmp_path):
    env.port_open = True
    m = env.manager(tmp_path)
    assert m.start() is ManagerState.EXTERNAL and m.external and m.ready
    assert env.launches == []
    m.stop()
    assert env.procs == [] and env.port_open  # we left it alone


def test_spawns_with_loopback_only_flags_and_app_config_dir(env, tmp_path):
    m = env.manager(tmp_path)
    assert m.start() is ManagerState.RUNNING
    args = env.launches[0]
    assert args[0] == str(env.exe)
    assert "--server" in args and "--startminimized" in args
    assert args[args.index("--server-host") + 1] == "127.0.0.1"
    assert args[args.index("--server-port") + 1] == "6742"
    assert args[args.index("--config") + 1] == str(tmp_path / "cfg")
    assert (tmp_path / "cfg").is_dir()


def test_stop_terminates_a_process_we_started(env, tmp_path):
    m = env.manager(tmp_path)
    m.start()
    m.stop()
    assert env.procs[0].terminated and not env.procs[0].killed
    assert m.state is ManagerState.STOPPED


def test_stop_kills_if_terminate_is_ignored(env, tmp_path, monkeypatch):
    m = env.manager(tmp_path)
    m.start()
    env.procs[0].ignore_terminate = True
    m.stop()
    assert env.procs[0].killed


def test_start_times_out_when_the_port_never_opens(env, tmp_path):
    env.opens_after_launch = False
    m = env.manager(tmp_path, start_timeout=15.0)
    assert m.start() is ManagerState.FAILED
    assert "15" in m.reason and env.procs[0].terminated  # and we cleaned up the stray process
    assert 15.0 <= env.now < 16.0


def test_start_fails_fast_if_the_process_dies_during_startup(env, tmp_path):
    env.opens_after_launch = False
    m = env.manager(tmp_path)
    orig = env.launcher

    def dying(args, cwd):
        p = orig(args, cwd)
        p.code = 3
        return p

    m._launcher = dying
    assert m.start() is ManagerState.FAILED and "code 3" in m.reason
    assert env.now < 1.0


def test_missing_exe_is_a_permanent_failure(env, tmp_path):
    m = OpenRGBManager(exe_path=tmp_path / "nope.exe", launcher=env.launcher, port_probe=env.probe,
                       clock=env.clock, sleep=env.sleep, config_dir=tmp_path / "cfg")
    # locate_exe falls back to vendor/, which may exist on a dev box; force "nothing found"
    import rgbapp.engine.openrgb_manager as mod
    orig = mod.locate_exe
    mod.locate_exe = lambda explicit=None: None
    try:
        assert m.start() is ManagerState.FAILED and "not found" in m.reason
        env.now += 1000
        m.supervise_once()
        assert env.launches == []  # does not keep retrying something that cannot work
    finally:
        mod.locate_exe = orig


def test_elevation_required_gives_an_actionable_permanent_failure(env, tmp_path):
    err = OSError(740, "The requested operation requires elevation")
    err.winerror = 740
    env.launch_error = err
    m = env.manager(tmp_path)
    assert m.start() is ManagerState.FAILED
    assert "administrator" in m.reason.lower()
    env.launch_error = None
    env.now += 100
    m.supervise_once()
    assert m.state is ManagerState.FAILED and env.launches == []


def test_supervisor_restarts_a_dead_process_with_backoff(env, tmp_path):
    m = env.manager(tmp_path)
    seen = []
    m.listeners.append(lambda s, r: seen.append(s))
    m.start()
    env.port_open = False
    env.procs[0].code = 1  # crashed
    m.supervise_once()
    assert m.state is ManagerState.STARTING and len(env.launches) == 1  # waits out the 1 s backoff
    env.now += 0.5
    m.supervise_once()
    assert len(env.launches) == 1
    env.now += 1.0
    m.supervise_once()
    assert len(env.launches) == 2 and m.state is ManagerState.RUNNING
    assert ManagerState.STARTING in seen and seen[-1] is ManagerState.RUNNING


def test_failed_restart_backs_off_further(env, tmp_path):
    m = env.manager(tmp_path)
    m.start()
    env.port_open = False
    env.opens_after_launch = False
    env.procs[0].code = 1
    m.supervise_once()  # STARTING, next try in 1 s
    delays = []
    for _ in range(4):
        t0 = env.now
        while len(env.launches) == len(delays) + 1:
            env.now += 0.25
            m.supervise_once()
        delays.append(env.now - t0)
        env.opens_after_launch = False
    # Each retry waits longer than the one before, up to the cap
    assert delays == sorted(delays)


def test_takes_over_when_an_external_server_goes_away(env, tmp_path):
    env.port_open = True
    m = env.manager(tmp_path)
    m.start()
    assert m.external
    env.port_open = False
    m.supervise_once()
    assert m.state is ManagerState.RUNNING and len(env.launches) == 1


def test_listeners_hear_state_changes_and_a_bad_listener_is_harmless(env, tmp_path):
    m = env.manager(tmp_path)
    seen = []
    m.listeners.append(lambda s, r: 1 / 0)
    m.listeners.append(lambda s, r: seen.append(s))
    m.start()
    assert seen == [ManagerState.STARTING, ManagerState.RUNNING]


def test_locate_exe_prefers_explicit_then_env(tmp_path, monkeypatch):
    a, b = tmp_path / "a.exe", tmp_path / "b.exe"
    a.write_bytes(b"")
    b.write_bytes(b"")
    monkeypatch.setenv("RGBAPP_OPENRGB", str(b))
    assert locate_exe(a) == a
    assert locate_exe(tmp_path / "missing.exe") == b
