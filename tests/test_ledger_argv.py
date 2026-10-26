"""ledger.py build argv: one parser, one usage string (review-code-cp5i.10, .14). main is driven with build mocked."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

import paths  # noqa: F401

import ledger  # noqa: E402

B = ["build", "--run", "R"]


def run_main(argv, stdin=""):
    out = io.StringIO()
    with mock.patch.object(ledger, "build", return_value={"status": "ok"}) as bld, \
            mock.patch("sys.stdin", io.StringIO(stdin)), redirect_stdout(out):
        rc = ledger.main(argv)
    return rc, json.loads(out.getvalue()), bld


class Argv(unittest.TestCase):
    def ok(self, extra, want, stdin=""):
        rc, out, bld = run_main(B + extra, stdin)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        (run, spec, found, nit, rubrics), _ = bld.call_args
        self.assertEqual((run, spec, list(found), nit, list(rubrics)), ("R", want[0], list(want[1]), want[2], list(want[3])))

    def usage(self, extra, stdin=""):
        rc, out, bld = run_main(B + extra, stdin)
        self.assertEqual((rc, out.get("reason")), (1, "usage"), out)
        bld.assert_not_called()

    def test_valid_forms(self):
        self.ok([], (None, (), False, ()))
        self.ok(["--nit"], (None, (), True, ()))
        self.ok(["--spec", "p.md"], ("p.md", (), False, ()))
        self.ok(["--spec", "p.md", "--nit"], ("p.md", (), True, ()))
        self.ok(["--found", "-", "--spec", "p.md"], ("p.md", ["x", "y"], False, ()), "x\ny\n")
        self.ok(["--spec", "-"], ("s.md", (), False, ()), "s.md\n")
        self.ok(["--rubric", "a.md", "--rubric", "b.md"], (None, (), False, ["a.md", "b.md"]))
        self.ok(["--nit", "--rubric", "a.md", "--spec", "p.md"], ("p.md", (), True, ["a.md"]))

    def test_invalid_forms_are_usage(self):
        for extra in (["--nit", "--nit"], ["--spec"], ["--spec", "a", "--spec", "b"], ["--found", "x"], ["--found"],
                      ["--rubric"], ["--bogus", "v"], ["--spec", "--nit"], ["stray"], ["--spec", "a", "stray"]):
            with self.subTest(extra=extra):
                self.usage(extra)
        rc, out, _ = run_main(["build", "--run"])
        self.assertEqual((rc, out["reason"]), (1, "usage"))

    def test_the_header_documents_the_build_flags_the_usage_string_names(self):
        self.assertIn("--rubric", ledger.BUILD_USAGE)
        self.assertIn(ledger.BUILD_USAGE, ledger.__doc__)


if __name__ == "__main__":
    unittest.main()
