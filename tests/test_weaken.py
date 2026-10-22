"""weaken.weakened: does an edit to a test file drop assertions or add skip markers."""
import unittest

import paths  # noqa: F401

import weaken  # noqa: E402


def w(path, pre, now):
    return weaken.weakened(path, pre.encode(), now.encode())


class Weakened(unittest.TestCase):
    def test_a_swapped_expected_value_passes(self):
        self.assertIsNone(w("tests/test_a.py", "def t():\n    assert f() == 1\n", "def t():\n    assert f() == 2\n"))

    def test_an_added_assertion_passes(self):
        self.assertIsNone(w("tests/test_a.py", "def t():\n    assert f()\n", "def t():\n    assert f()\n    assert g()\n"))

    def test_a_removed_assertion_is_weakening(self):
        self.assertIn("assertion", w("tests/test_a.py", "def t():\n    assert f()\n    assert g()\n", "def t():\n    assert f()\n"))

    def test_a_moved_assertion_passes(self):
        self.assertIsNone(w("tests/test_a.py", "assert a()\nx = 1\n", "x = 1\nassert a()\n"))

    def test_an_assertion_turned_into_a_comment_is_weakening(self):
        self.assertTrue(w("tests/test_a.py", "assert f()\n", "# assert f()\n"))

    def test_a_commented_assertion_added_does_not_count(self):
        self.assertTrue(w("tests/test_a.py", "assert f()\nassert g()\n", "assert f()\n# assert g()\n"))

    def test_an_added_skip_marker_is_weakening(self):
        for path, pre, now in (
                ("tests/test_a.py", "def t():\n    assert 1\n", "@pytest.mark.skip\ndef t():\n    assert 1\n"),
                ("tests/test_a.py", "def t():\n    assert 1\n", "@pytest.mark.xfail\ndef t():\n    assert 1\n"),
                ("a.test.js", "it('x', () => { expect(1) })\n", "it.skip('x', () => { expect(1) })\n"),
                ("a.test.js", "it('x', () => { expect(1) })\n", "it.only('x', () => { expect(1) })\n"),
                ("A.java", "@Test void x() { assertTrue(a); }\n", "@Ignore @Test void x() { assertTrue(a); }\n"),
                ("a_test.go", "func T(t *testing.T) { assert.Equal(t, 1, a) }\n",
                 "func T(t *testing.T) { t.Skip(); assert.Equal(t, 1, a) }\n"),
                ("a_spec.rb", "it 'x' do\n  expect(a).to eq 1\nend\n", "xit 'x' do\n  expect(a).to eq 1\nend\n")):
            with self.subTest(path=path, now=now):
                self.assertIn("skip", w(path, pre, now))

    def test_unittest_and_mocha_skip_calls_are_skip_markers(self):
        for path, pre, now in (
                ("tests/test_a.py", "def t(self):\n    assert 1\n", "def t(self):\n    self.skipTest('x')\n    assert 1\n"),
                ("tests/test_a.py", "def t(self):\n    assert 1\n", "def t(self):\n    raise unittest.SkipTest\n    assert 1\n"),
                ("a.test.js", "it('x', function () { expect(1) })\n", "it('x', function () { this.skip(); expect(1) })\n")):
            with self.subTest(path=path, now=now):
                self.assertIn("skip", w(path, pre, now))

    def test_unknown_suffix_rejects_an_added_skip_or_exit(self):
        self.assertIn("skip", w("tests/testthat/test-a.R", "expect_equal(a, 1)\n", "skip('x')\nexpect_equal(a, 1)\n"))
        self.assertIn("skip", w("tests/check.sh", "run a\n", "exit 0\nrun a\n"))

    def test_a_removed_skip_marker_passes(self):
        self.assertIsNone(w("tests/test_a.py", "@pytest.mark.skip\ndef t():\n    assert 1\n", "def t():\n    assert 1\n"))

    def test_unknown_suffix_rejects_any_removed_line(self):
        self.assertTrue(w("tests/test_a.r", "expect_equal(a, 1)\nx\n", "x\n"))
        self.assertTrue(w("tests/test_a.r", "expect_equal(a, 1)\n", "expect_equal(a, 2)\n"))  # a modified line is a removed one

    def test_unknown_suffix_accepts_pure_insertion(self):
        self.assertIsNone(w("tests/test_a.r", "expect_equal(a, 1)\n", "expect_equal(a, 1)\nexpect_equal(b, 2)\n"))

    def test_a_new_file_or_deleted_file_is_handled(self):
        self.assertIsNone(w("tests/test_a.py", "", "assert 1\n"))
        self.assertTrue(w("tests/test_a.py", "assert 1\n", ""))

    def test_non_utf8_bytes_do_not_raise(self):
        self.assertIsNone(weaken.weakened("tests/test_a.py", b"assert '\xff'\n", b"assert '\xfe'\n"))


if __name__ == "__main__":
    unittest.main()
