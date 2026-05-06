from __future__ import annotations

import inspect
import json
import os
import sys

from meadowpy.core import debug_helper


class CapturingSocket:
    def __init__(self, chunks=None):
        self.chunks = list(chunks or [])
        self.sent = []

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, size):
        if self.chunks:
            return self.chunks.pop(0)
        return b""


def test_send_serializes_single_newline_delimited_json_message():
    sock = CapturingSocket()

    debug_helper._send(sock, {"event": "paused", "name": "Ada"})

    assert sock.sent == [b'{"event": "paused", "name": "Ada"}\n']


def test_debugger_updates_breakpoints_and_detects_breakpoint_lines(tmp_path):
    script = tmp_path / "demo.py"
    script.write_text("print('one')\nprint('two')\n", encoding="utf-8")
    debugger = debug_helper.MeadowPyDebugger(CapturingSocket())

    debugger._update_breakpoints({str(script): [2]})

    assert debugger._has_breakpoint(str(script), 2) is True
    assert debugger._has_breakpoint(str(script), 1) is False


def test_command_loop_handles_evaluate_then_continue():
    sock = CapturingSocket([
        b'{"cmd":"evaluate","expression":"value + 5","frame_index":0}\n',
        b'{"cmd":"continue"}\n',
    ])
    debugger = debug_helper.MeadowPyDebugger(sock)
    continued = []
    debugger.set_continue = lambda: continued.append(True)

    def sample():
        value = 37
        frame = inspect.currentframe()
        debugger._command_loop(frame)

    sample()

    payload = json.loads(sock.sent[0].decode("utf-8"))
    assert payload == {
        "event": "eval_result",
        "expression": "value + 5",
        "result": "42",
        "error": None,
    }
    assert continued == [True]


def test_command_loop_ignores_invalid_json_and_handles_resume_commands():
    commands = [
        ("step_over", "next"),
        ("step_into", "step"),
        ("step_out", "return"),
        ("disconnect", "continue"),
    ]

    for command, expected in commands:
        sock = CapturingSocket([b"not-json\n", f'{{"cmd":"{command}"}}\n'.encode()])
        debugger = debug_helper.MeadowPyDebugger(sock)
        calls = []
        debugger.set_next = lambda frame, calls=calls: calls.append("next")
        debugger.set_step = lambda calls=calls: calls.append("step")
        debugger.set_return = lambda frame, calls=calls: calls.append("return")
        debugger.set_continue = lambda calls=calls: calls.append("continue")

        frame = inspect.currentframe()
        debugger._command_loop(frame)

        assert calls == [expected]


def test_send_pause_emits_variables_and_call_stack():
    sock = CapturingSocket()
    debugger = debug_helper.MeadowPyDebugger(sock)

    def sample():
        local_value = "visible"
        frame = inspect.currentframe()
        debugger._send_pause(frame, "step")

    sample()

    payload = json.loads(sock.sent[0].decode("utf-8"))
    assert payload["event"] == "paused"
    assert payload["reason"] == "step"
    assert payload["variables"]["locals"]["local_value"] == "'visible'"
    assert payload["call_stack"][0]["function"] == "sample"


def test_user_line_initial_continue_skips_until_breakpoint(tmp_path):
    script = tmp_path / "demo.py"
    script.write_text("print('one')\n", encoding="utf-8")
    debugger = debug_helper.MeadowPyDebugger(CapturingSocket())
    pauses = []
    commands = []
    debugger.botframe = None
    debugger._send_pause = lambda frame, reason: pauses.append((frame.f_lineno, reason))
    debugger._command_loop = lambda frame: commands.append(frame.f_lineno)

    def sample():
        frame = inspect.currentframe()
        debugger.user_line(frame)
        norm = os.path.normcase(os.path.abspath(frame.f_code.co_filename))
        debugger._breakpoints_map[norm] = set(range(frame.f_lineno, frame.f_lineno + 6))
        debugger.user_line(frame)

    sample()

    assert len(pauses) == 1
    assert pauses[0][1] == "breakpoint"
    assert commands == [pauses[0][0]]
    assert debugger._initial_continue is False


def test_main_exits_with_usage_when_arguments_are_missing(monkeypatch, capsys):
    monkeypatch.setattr(debug_helper.sys, "argv", ["debug_helper.py"])

    try:
        debug_helper.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("main() should exit for missing arguments")

    assert "Usage: python debug_helper.py" in capsys.readouterr().err


def test_main_exits_when_socket_connection_fails(monkeypatch, capsys):
    class FailingSocket:
        def connect(self, address):
            raise OSError("refused")

    monkeypatch.setattr(debug_helper.sys, "argv", ["debug_helper.py", "4321", "demo.py"])
    monkeypatch.setattr(debug_helper.socket, "socket", lambda *args, **kwargs: FailingSocket())

    try:
        debug_helper.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("main() should exit when it cannot connect")

    assert "cannot connect to IDE on port 4321" in capsys.readouterr().err


def test_main_sets_breakpoints_runs_script_and_sends_finished(monkeypatch, tmp_path):
    script = tmp_path / "target.py"
    script.write_text("value = 42\n", encoding="utf-8")
    sent = []
    debugger_records = []

    class FakeSocket:
        def __init__(self):
            self.connected_to = None
            self.closed = False

        def connect(self, address):
            self.connected_to = address

        def close(self):
            self.closed = True

    class FakeDebugger:
        def __init__(self, sock):
            self.sock = sock
            self._buf = None
            debugger_records.append(("init", sock))

        def _update_breakpoints(self, breakpoints):
            debugger_records.append(("breakpoints", breakpoints))

        def run(self, code, globals_dict):
            debugger_records.append((
                "run",
                globals_dict["__name__"],
                globals_dict["__file__"],
                "value" in code.co_names,
            ))

    fake_socket = FakeSocket()
    monkeypatch.setattr(
        debug_helper.sys,
        "argv",
        ["debug_helper.py", "8765", str(script), "arg1"],
    )
    monkeypatch.setattr(
        debug_helper.socket,
        "socket",
        lambda *args, **kwargs: fake_socket,
    )
    monkeypatch.setattr(
        debug_helper,
        "_send",
        lambda sock, payload: sent.append(payload),
    )
    monkeypatch.setattr(
        debug_helper,
        "_recv_line",
        lambda sock, buf: json.dumps({
            "cmd": "set_breakpoints",
            "breakpoints": {str(script): [1]},
        }),
    )
    monkeypatch.setattr(debug_helper, "MeadowPyDebugger", FakeDebugger)
    old_path = list(sys.path)

    try:
        debug_helper.main()
    finally:
        sys.path[:] = old_path

    assert fake_socket.connected_to == ("127.0.0.1", 8765)
    assert debug_helper.sys.argv == [str(script), "arg1"]
    assert sent == [
        {"event": "connected"},
        {"event": "finished", "reason": "completed"},
    ]
    assert debugger_records[1] == ("breakpoints", {str(script): [1]})
    assert debugger_records[2] == ("run", "__main__", str(script), True)
    assert fake_socket.closed is True
