"""faces/: every asset servable, nothing the door's CSP would refuse, the shared kit identical.

gonk serves a face from one directory under `script-src 'self'` with no inline script or style
(faces/README.md). A browser drops what that CSP refuses without failing the page, so the
check is here: no inline <script>, no <style> element, no style= or on…= attribute, and only
the extensions a face serves. No browser runs here; ACCESSIBILITY.md says what is not tested.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FACES = ROOT / "faces"
sys.path.insert(0, str(ROOT / "tools"))

import gonk  # noqa: E402
import new  # noqa: E402

# Copied from ikigai-gonk src/face.rs MEDIA_TYPES at e08ac9f.
# https://github.com/ikigai-rs/ikigai-gonk/blob/e08ac9f/src/face.rs
ALLOWED = {
    ".html",
    ".css",
    ".js",
    ".mjs",
    ".json",
    ".jsonld",
    ".ttl",
    ".txt",
    ".svg",
    ".png",
    ".jpg",
    ".webp",
    ".ico",
    ".woff2",
}
SAMPLES = ["template", "board", "flight", "project", "claims"]
SHARED = ["kit.js", "app.css"]
THEMES = ["light", "dark", "high-contrast"]
TEXT_TOKENS = ["fg", "muted", "accent", "warn", "ok"]
NON_TEXT_TOKENS = [
    "line",
    "focus",
    "column-blocked",
    "column-ready",
    "column-flight",
    "column-awaiting",
    "column-done",
]


class CspProblems(HTMLParser):
    """What the face CSP would refuse in one HTML file, as sentences."""

    def __init__(self) -> None:
        super().__init__()
        self.problems: list[str] = []
        self.in_script = False
        self.lang = None

    def handle_starttag(self, tag, attrs):
        names = {k.lower(): v for k, v in attrs}
        if tag == "html":
            self.lang = names.get("lang")
        if "style" in names:
            self.problems.append(f"<{tag}> has a style= attribute")
        for k in names:
            if k.startswith("on"):
                self.problems.append(f"<{tag}> has an inline handler {k}=")
        if tag == "style":
            self.problems.append("a <style> element")
        if tag == "script":
            if not names.get("src"):
                self.problems.append("an inline <script> (no src)")
            elif "//" in (names.get("src") or ""):
                self.problems.append(f"a script from another origin: {names['src']}")
            self.in_script = True
        if tag == "link" and "//" in (names.get("href") or ""):
            self.problems.append(f"a link to another origin: {names['href']}")

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.in_script and data.strip():
            self.problems.append("a <script> with inline content")


def problems(html: str) -> list[str]:
    p = CspProblems()
    p.feed(html)
    return p.problems


def luminance(color: str) -> float:
    """WCAG relative luminance of a #rrggbb color."""
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two #rrggbb colors."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def tokens(block: str) -> dict[str, str]:
    """The color tokens one rule of app.css defines, by name."""
    return dict(re.findall(r"--([a-z-]+): (#[0-9a-f]{6});", block))


def theme_tokens(css: str, name: str) -> dict[str, str]:
    """The tokens of the rule `:root[data-theme="<name>"]`."""
    found = re.search(rf':root\[data-theme="{name}"\] \{{(.*?)\}}', css, re.S)
    return tokens(found.group(1)) if found else {}


def auto_tokens(css: str, feature: str) -> dict[str, str]:
    """The tokens for "auto" (or no attribute) under one media feature."""
    selector = r':root:not\(\[data-theme\]\), :root\[data-theme="auto"\]'
    found = re.search(rf"@media \({feature}\) \{{\s*{selector} \{{(.*?)\}}", css, re.S)
    return tokens(found.group(1)) if found else {}


NAMED_COLOR = re.compile(
    r"\b(white|black|red|green|blue|yellow|orange|purple|pink|brown|gray|grey|silver|navy|teal"
    r"|maroon|lime|aqua|fuchsia|olive|canvas|canvastext)\b",
    re.I,
)


def colors_outside_tokens(css: str) -> list[str]:
    """Every declaration of a stylesheet that writes a color instead of naming a token."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    found = []
    for name, value in re.findall(r"([a-z-]+)\s*:\s*([^;{}]+)[;}]", css):
        if name.startswith("--") or name == "color-scheme":
            continue
        if re.search(
            r"#[0-9a-f]{3,8}\b|\b(rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch)\(", value, re.I
        ):
            found.append(f"{name}: {value.strip()}")
        elif NAMED_COLOR.search(value):
            found.append(f"{name}: {value.strip()}")
    return found


class FacesTest(unittest.TestCase):
    def files(self) -> list[Path]:
        return [p for p in FACES.rglob("*") if p.is_file()]

    def test_every_sample_is_there_with_an_entry_page(self):
        for name in SAMPLES:
            for f in ["index.html", "app.js", "README.md", *SHARED]:
                self.assertTrue((FACES / name / f).is_file(), f"faces/{name}/{f} is missing")

    def test_readme_lists_exactly_the_allowed_extensions(self):
        readme = (FACES / "README.md").read_text(encoding="utf-8")
        may = readme.split("- **May**:", 1)[1].split("- **May not**:", 1)[0]
        self.assertEqual(set(re.findall(r"`(\.[a-z0-9]+)`", may)), ALLOWED)

    def test_readme_uses_the_face_route_with_a_trailing_slash(self):
        readme = (FACES / "README.md").read_text(encoding="utf-8")
        self.assertIn("`http://127.0.0.1:1062/face/name/<name>/`", readme)

    def test_only_allowed_extensions(self):
        # READMEs document the source directories; gonk does not serve them.
        bad = [
            str(p.relative_to(ROOT))
            for p in self.files()
            if p.name != "README.md" and p.suffix not in ALLOWED
        ]
        self.assertEqual(bad, [], f"a face serves only {sorted(ALLOWED)}")

    def test_no_html_carries_what_the_csp_refuses(self):
        for page in FACES.rglob("*.html"):
            with self.subTest(page=str(page.relative_to(ROOT))):
                self.assertEqual(problems(page.read_text(encoding="utf-8")), [])

    def test_the_check_catches_each_refusal(self):
        self.assertEqual(len(problems("<script>alert(1)</script>")), 2)
        self.assertEqual(problems('<p style="color:red">x</p>'), ["<p> has a style= attribute"])
        self.assertEqual(problems("<style>p{}</style>"), ["a <style> element"])
        self.assertEqual(problems('<a onclick="x()">x</a>'), ["<a> has an inline handler onclick="])
        self.assertEqual(problems('<script type="module" src="app.js"></script>'), [])

    def test_pages_have_a_language_a_heading_and_a_polite_status(self):
        for page in FACES.rglob("index.html"):
            text = page.read_text(encoding="utf-8")
            with self.subTest(page=str(page.relative_to(ROOT))):
                self.assertIn('<html lang="en">', text)
                self.assertIn("<h1>", text)
                self.assertIn('role="status" aria-live="polite"', text)

    def test_shared_files_are_identical_to_the_templates(self):
        for name in SAMPLES[1:]:
            for f in SHARED:
                with self.subTest(face=name, file=f):
                    self.assertEqual(
                        (FACES / name / f).read_bytes(),
                        (FACES / "template" / f).read_bytes(),
                        f"faces/{name}/{f} differs: cp faces/template/{f} faces/{name}/",
                    )

    def test_no_style_set_from_script(self):
        for js in FACES.rglob("*.js"):
            text = js.read_text(encoding="utf-8")
            with self.subTest(file=str(js.relative_to(ROOT))):
                self.assertNotIn(".style.", text)
                self.assertNotIn("innerHTML", text)
                self.assertNotIn("http://", text)
                self.assertNotIn("https://", text)

    def test_a_face_never_appends_a_maybe_absent_child_to_the_page_directly(self):
        # DOM `append(null)` writes the text "null" on the page, and every face passes children
        # like `cond ? x : null` (it did: "null" under the board's heading, "nullnull" under a
        # flight plan). Output goes through kit's `append`, which skips them as `el` does.
        for js in FACES.rglob("app.js"):
            text = js.read_text(encoding="utf-8")
            with self.subTest(file=str(js.relative_to(ROOT))):
                self.assertNotIn("out.append(", text)
        for kit in FACES.rglob("kit.js"):
            with self.subTest(file=str(kit.relative_to(ROOT))):
                text = kit.read_text(encoding="utf-8")
                self.assertIn("export function append(node, ...children)", text)

    def css(self) -> str:
        return (FACES / "template" / "app.css").read_text(encoding="utf-8")

    def test_every_theme_defines_the_same_tokens_and_auto_falls_back_to_them(self):
        css = self.css()
        light = theme_tokens(css, "light")
        self.assertTrue(light, "no light theme in app.css")
        for name in THEMES:
            with self.subTest(theme=name):
                self.assertEqual(theme_tokens(css, name).keys(), light.keys())
        # With no attribute, or "auto", the system settings choose among the same palettes.
        default = css.split("@media", 1)[0]
        self.assertEqual(tokens(default), light)
        self.assertEqual(auto_tokens(css, "prefers-color-scheme: dark"), theme_tokens(css, "dark"))
        self.assertEqual(
            auto_tokens(css, "prefers-contrast: more"), theme_tokens(css, "high-contrast")
        )
        # Equal specificity, so the later rule wins: high contrast must follow dark, as in gonk's.
        self.assertLess(
            css.index("@media (prefers-color-scheme: dark)"),
            css.index("@media (prefers-contrast: more)"),
        )

    def test_text_and_non_text_tokens_meet_their_floors_in_every_theme(self):
        css = self.css()
        for name in THEMES:
            palette = theme_tokens(css, name)
            floor = 7.0 if name == "high-contrast" else 4.5
            for ground in ["bg", "surface"]:
                for token in TEXT_TOKENS:
                    with self.subTest(theme=name, token=token, against=ground):
                        self.assertGreaterEqual(contrast(palette[token], palette[ground]), floor)
                for token in NON_TEXT_TOKENS:
                    with self.subTest(theme=name, token=token, against=ground):
                        self.assertGreaterEqual(contrast(palette[token], palette[ground]), 3.0)

    def test_every_color_in_app_css_comes_from_the_tokens(self):
        self.assertEqual(colors_outside_tokens(self.css()), [])

    def test_the_color_check_catches_a_written_color(self):
        self.assertEqual(colors_outside_tokens("a { color: #123456; }"), ["color: #123456"])
        self.assertEqual(colors_outside_tokens("a { color: white }"), ["color: white"])
        self.assertEqual(
            colors_outside_tokens("a { background: rgb(0, 0, 0); }"),
            ["background: rgb(0, 0, 0)"],
        )
        self.assertEqual(colors_outside_tokens(":root { --bg: #fff; color: var(--bg); }"), [])

    def test_no_face_carries_a_way_back_to_gonk_of_its_own(self):
        # gonk draws the frame around a face (ledger item 3279): a breadcrumb here would be a
        # second one on the same page.
        for page in FACES.rglob("index.html"):
            text = page.read_text(encoding="utf-8")
            with self.subTest(page=str(page.relative_to(ROOT))):
                self.assertNotIn('aria-label="Breadcrumb"', text)
                self.assertNotIn('href="/"', text)

    def test_a_table_does_not_inherit_breaking_anywhere(self):
        # The page's `overflow-wrap: anywhere` broke narrow columns one character per line.
        self.assertRegex(self.css(), r"table \{[^}]*overflow-wrap: normal;")

    def test_projects_json_parses_to_names_and_ledgers(self):
        projects = json.loads((FACES / "project" / "projects.json").read_text(encoding="utf-8"))
        self.assertTrue(projects)
        for name, ledgers in projects.items():
            self.assertIsInstance(name, str)
            self.assertTrue(ledgers and all(isinstance(x, str) for x in ledgers))


class FaceLogicTest(unittest.TestCase):
    """The board's and flight's logic (board.js, plan.js, kit.js) against a recorded item.

    tests/faces/logic_test.js runs under Deno, which scripts/gates.sh provisions; run alone
    without Deno on PATH, this test skips and says so.
    """

    def test_board_and_flight_against_the_recorded_item(self):
        deno = shutil.which("deno")
        if not deno:
            self.skipTest("no deno on PATH (scripts/gates.sh provisions one)")
        run = subprocess.run(
            [deno, "test", "--no-config", "--no-lock", "--quiet", "tests/faces/"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_no_face_lists_a_ledger_to_find_members(self):
        # An item listing answers open items only: a scan for `parent` links misses every
        # closed child. Members come from `item.children` and `item.links`.
        for name in ["board", "flight"]:
            for js in (FACES / name).glob("*.js"):
                if js.name == "kit.js":
                    continue
                with self.subTest(file=str(js.relative_to(ROOT))):
                    self.assertNotIn("listAll", js.read_text(encoding="utf-8"))
                    self.assertNotIn('"child"', js.read_text(encoding="utf-8"))


class FaceLineTest(unittest.TestCase):
    def test_init_face_line_names_the_directory(self):
        line = gonk.face_line(ROOT, "board")
        self.assertEqual(line, f'gonk.face = "board {(FACES / "board").resolve()}"')

    def test_init_face_refuses_an_unknown_face(self):
        with self.assertRaises(gonk.ConfigError) as caught:
            gonk.face_line(ROOT, "nope")
        self.assertIn("board", str(caught.exception))

    def test_new_face_copies_faces_and_prints_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "repo"
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(new.main([str(dest), "--face", "claims"]), 0)
            self.assertTrue((dest / "faces" / "claims" / "index.html").is_file())
            self.assertTrue((dest / "faces" / "template" / "kit.js").is_file())
            self.assertIn(
                f'gonk.face = "claims {dest.resolve() / "faces" / "claims"}"', out.getvalue()
            )
            self.assertIn("faces/claims", gonk.face_line(dest, "claims"))


if __name__ == "__main__":
    unittest.main()
