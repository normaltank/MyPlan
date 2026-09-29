"""The degraded path must keep working when the L2 algorithm layer is missing.

server/faculty_extractor guards its algorithm-layer imports with `except ImportError`.
Adding `strip_invisible` to that multi-name import (for zero-width name
normalisation) nearly broke it: the handler rebound the other names but not this
one, so `_clean_name` raised NameError on every extraction as soon as the
optional layer was unavailable — a defensive change taking the project down.

This runs the failure in a subprocess because the module caches its import state.
"""
import subprocess
import sys
import textwrap

SIMULATION = textwrap.dedent('''
    import builtins, sys
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in {"iso_mining", "email_matching", "text_lists"}:
            raise ImportError("simulated missing algorithm layer")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = fake_import
    sys.path.insert(0, ".")
    import server.faculty_extractor as fe

    assert fe.L2_AVAILABLE is False, "simulation did not reach the degraded path"
    cleaned, title = fe.FacultyDirectoryExtractor._clean_name("张\u200b国进")
    assert cleaned == "张国进", cleaned
    html = '<html><body><ul><li><a href="/p/1.htm">王伟</a><span>教授</span></li>' * 4 + "</ul></body></html>"
    candidates = fe.FacultyDirectoryExtractor.extract(html, "http://x.edu.cn/list.htm")
    assert candidates, "degraded extraction returned nothing"
    print("DEGRADED_OK", len(candidates))
''')


def test_degraded_path_still_extracts_without_the_algorithm_layer():
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", SIMULATION],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", cwd=".")
    assert "DEGRADED_OK" in result.stdout, (
        f"degraded path broken\nstdout={result.stdout[-600:]}\nstderr={result.stderr[-600:]}")
