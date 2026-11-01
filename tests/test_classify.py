import unittest

from churn.classify import COMMENT, FORMATTING, LOGIC, UNPARSED, Hunk, classify_hunks


def replace(line_no, *new):
    return Hunk(line_no, 1, [line + "\n" for line in new])


class ClassifyHunks(unittest.TestCase):
    def test_quote_change_is_formatting(self):
        self.assertEqual(classify_hunks("x = 'a'\n", 'x = "a"\n', [replace(1, 'x = "a"')]), [FORMATTING])

    def test_line_split_and_trailing_comma_is_formatting(self):
        old = "f(a, b)\n"
        new = "f(\n    a,\n    b,\n)\n"
        self.assertEqual(classify_hunks(old, new, [replace(1, "f(", "    a,", "    b,", ")")]), [FORMATTING])

    def test_blank_line_insert_is_formatting(self):
        old = "a = 1\nb = 2\n"
        hunk = Hunk(1, 0, ["\n"])
        self.assertEqual(classify_hunks(old, "a = 1\n\nb = 2\n", [hunk]), [FORMATTING])

    def test_value_change_is_logic(self):
        self.assertEqual(classify_hunks("x = 1\n", "x = 2\n", [replace(1, "x = 2")]), [LOGIC])

    def test_comment_text_change_is_comment(self):
        old = "# old\nx = 1\n"
        self.assertEqual(classify_hunks(old, "# new\nx = 1\n", [replace(1, "# new")]), [COMMENT])

    def test_comment_spacing_is_formatting(self):
        old = "#note\nx = 1\n"
        self.assertEqual(classify_hunks(old, "# note\nx = 1\n", [replace(1, "# note")]), [FORMATTING])

    def test_docstring_indent_is_formatting_but_text_is_logic(self):
        old = 'def f():\n    """a\n      b"""\n'
        self.assertEqual(
            classify_hunks(old, 'def f():\n    """a\n    b"""\n', [replace(3, '    b"""')]), [FORMATTING]
        )
        self.assertEqual(classify_hunks(old, 'def f():\n    """a\n    c"""\n', [replace(3, '    c"""')]), [LOGIC])

    def test_mixed_commit_is_split_by_hunk(self):
        old = "x = 'a'\n\ny = 1\n"
        new = 'x = "a"\n\ny = 2\n'
        self.assertEqual(classify_hunks(old, new, [replace(1, 'x = "a"'), replace(3, "y = 2")]), [FORMATTING, LOGIC])

    def test_python2_source_is_unparsed(self):
        self.assertEqual(classify_hunks("print 'a'\n", 'print "a"\n', [replace(1, 'print "a"')]), [UNPARSED])


if __name__ == "__main__":
    unittest.main()
