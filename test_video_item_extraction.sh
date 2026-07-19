#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from bs4 import BeautifulSoup

source_path = Path("main.py")
tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
needed_names = {
    "get_img_src",
    "is_supported_story_url",
    "is_unwanted_strict",
    "extract_from_item",
}
module_body = [
    ast.Import(names=[ast.alias(name="re")]),
    ast.ImportFrom(module="pathlib", names=[ast.alias(name="Path")], level=0),
    ast.ImportFrom(
        module="urllib.parse",
        names=[ast.alias(name="urljoin"), ast.alias(name="urlparse")],
        level=0,
    ),
    ast.Assign(
        targets=[ast.Name(id="URL_BLACKLIST_SUBSTR", ctx=ast.Store())],
        value=ast.List(elts=[], ctx=ast.Load()),
    ),
    ast.Assign(
        targets=[ast.Name(id="DATA_TESTID_EXACT", ctx=ast.Store())],
        value=ast.Set(elts=[
            ast.Constant("live"),
            ast.Constant("interactive"),
            ast.Constant("load-more-posts"),
            ast.Constant("load-more"),
        ]),
    ),
    ast.Assign(
        targets=[ast.Name(id="TITLE_BLACKLIST", ctx=ast.Store())],
        value=ast.List(elts=[], ctx=ast.Load()),
    ),
]

for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in needed_names:
        module_body.append(node)

test_module = ast.Module(body=module_body, type_ignores=[])
ast.fix_missing_locations(test_module)
namespace = {}
exec(compile(test_module, str(source_path), "exec"), namespace)
extract_from_item = namespace["extract_from_item"]
is_supported_story_url = namespace["is_supported_story_url"]

html = """
<li>
  <article>
    <div data-testid="video"><span>Times</span><span>Video</span></div>
    <a href="/video/world/asia/100000011031760/tokyo-shorts-government-workers-summer.html">
      <h3>Tokyo Officials Ditch Suits for Shorts in Summer Heat</h3>
    </a>
    <p>Tokyo’s government has a new summer dress code allowing shorts and T-shirts at work.</p>
    <img src="https://static01.nyt.com/images/2026/07/16/multimedia/example-square320.jpg" />
  </article>
</li>
<li>
  <article>
    <a href="/2026/07/18/world/canada/canada-wildfires-what-to-know.html">
      <h3>Canada Wildfires: What to Know</h3>
    </a>
    <p>Hundreds of wildfires have blanketed large sections of North America.</p>
  </article>
</li>
<li>
  <article>
    <a href="/section/world"><h3>World News</h3></a>
  </article>
</li>
"""
soup = BeautifulSoup(html, "html.parser")
items = soup.select("li")

video = extract_from_item(items[0], "https://www.nytimes.com/section/world")
article = extract_from_item(items[1], "https://www.nytimes.com/section/world")
section = extract_from_item(items[2], "https://www.nytimes.com/section/world")

assert is_supported_story_url("https://www.nytimes.com/video/world/asia/100000011031760/example.html")
assert is_supported_story_url("https://www.nytimes.com/2026/07/18/world/example.html")
assert not is_supported_story_url("https://www.nytimes.com/section/world")

assert video is not None, "Times/Video story must be extracted"
assert video["title"] == "Tokyo Officials Ditch Suits for Shorts in Summer Heat"
assert video["url"] == "https://www.nytimes.com/video/world/asia/100000011031760/tokyo-shorts-government-workers-summer.html"
assert video["summary"].startswith("Tokyo’s government")

assert article is not None, "dated article story must still be extracted"
assert article["date"] == "2026-07-18"
assert section is None, "section navigation links must still be rejected"

print("Video item extraction check passed.")
PY
