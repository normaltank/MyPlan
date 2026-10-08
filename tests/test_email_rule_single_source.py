"""Address reading and ownership have one implementation, with no looser stand-in."""
import subprocess
import sys
import textwrap

import server.detail_mecc as detail_mecc
import server.faculty_extractor as faculty_extractor


def test_faculty_extractor_reexports_the_detail_mecc_email_tools():
    assert faculty_extractor.find_emails is detail_mecc.find_emails
    assert faculty_extractor.is_personal_email_for_scholar is detail_mecc.is_personal_email_for_scholar
    assert faculty_extractor.is_institutional_email is detail_mecc.is_institutional_email


def test_unused_free_mail_lists_are_gone():
    assert not hasattr(faculty_extractor, "FREE_MAIL_DOMAINS")
    assert not hasattr(detail_mecc, "FREE_MAIL_DOMAINS")


def test_missing_algorithm_layer_fails_the_import_instead_of_degrading():
    # Runs in a subprocess because the module caches its import state.
    simulation = textwrap.dedent('''
        import builtins, sys
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name in {"iso_mining", "email_matching", "text_lists"}:
                raise ImportError("simulated missing algorithm layer")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = fake_import
        sys.path.insert(0, ".")
        try:
            import server.faculty_extractor
        except ImportError:
            print("IMPORT_FAILED")
        else:
            print("IMPORTED")
    ''')
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", simulation],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", cwd=".")
    assert "IMPORT_FAILED" in result.stdout, (
        f"stdout={result.stdout[-600:]}\nstderr={result.stderr[-600:]}")
