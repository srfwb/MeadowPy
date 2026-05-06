from __future__ import annotations

from types import SimpleNamespace

from PyQt6.Qsci import QsciScintilla

from meadowpy.core.settings import Settings
from meadowpy.editor.code_editor import CodeEditor
from meadowpy.editor.editor_config import EditorConfigurator


def make_editor(qapp, tmp_path) -> CodeEditor:
    settings = Settings(tmp_path)
    settings.set("editor.auto_complete", False)
    editor = CodeEditor(settings)
    return editor


def test_toggle_comment_comments_and_uncomments_selected_block(qapp, tmp_path):
    editor = make_editor(qapp, tmp_path)
    editor.setText("x = 1\n    y = 2\n\n")
    editor.setSelection(0, 0, 1, len("    y = 2"))

    editor.toggle_comment()

    assert editor.text(0).startswith("# x = 1")
    assert editor.text(1).startswith("#     y = 2")
    assert editor._selection_is_commented() is True

    editor.toggle_comment()

    assert editor.text(0).startswith("x = 1")
    assert editor.text(1).startswith("    y = 2")
    editor.deleteLater()


def test_toggle_comment_uses_current_line_when_nothing_is_selected(qapp, tmp_path):
    editor = make_editor(qapp, tmp_path)
    editor.setText("print('hi')\n")
    editor.setCursorPosition(0, 0)

    editor.toggle_comment()
    assert editor.text(0).startswith("# print('hi')")

    editor.toggle_comment()
    assert editor.text(0).startswith("print('hi')")
    editor.deleteLater()


def test_find_enclosing_def_returns_prompt_code_and_docstring_line(qapp, tmp_path):
    editor = make_editor(qapp, tmp_path)
    editor.setText(
        "def greet(name):\n"
        "    value = name.upper()\n"
        "    return value\n"
        "\n"
        "print(greet('Ada'))\n"
    )

    func_code, insert_line = editor._find_enclosing_def(2)

    assert insert_line == 1
    assert "def greet(name):" in func_code
    assert "return value" in func_code
    editor.deleteLater()


def test_breakpoint_current_line_and_lint_helpers_track_editor_state(qapp, tmp_path):
    editor = make_editor(qapp, tmp_path)
    editor.setText("print(missing)\n")

    editor.toggle_breakpoint(0)
    assert editor.get_breakpoints() == {0}
    editor.toggle_breakpoint(0)
    assert editor.get_breakpoints() == set()

    editor.set_current_line(0)
    editor.clear_current_line()

    issue = SimpleNamespace(
        line=0,
        column=0,
        code="F821",
        message="undefined name 'missing'",
        severity="error",
    )
    editor.set_lint_issues([issue])
    assert "F821: undefined name" in editor._get_lint_tooltip(0, 2)

    editor.refresh_lint_colors()
    editor.clear_lint_markers()

    assert editor._get_lint_tooltip(0, 2) is None
    editor.deleteLater()


def test_editor_configurator_applies_disabled_editor_features(qapp, tmp_path):
    settings = Settings(tmp_path)
    settings.set("editor.auto_complete", False)
    settings.set("editor.word_wrap", False)
    settings.set("editor.brace_matching", False)
    settings.set("editor.show_line_numbers", False)
    settings.set("editor.code_folding", False)
    settings.set("editor.show_whitespace", True)
    settings.set("editor.theme", "default_high_contrast")

    editor = CodeEditor(settings)
    EditorConfigurator.apply(editor, settings)

    assert editor.wrapMode() == QsciScintilla.WrapMode.WrapNone
    assert editor.marginWidth(0) == 0
    assert editor.lexer() is not None
    assert not hasattr(editor, "_completion_apis")
    editor.deleteLater()
