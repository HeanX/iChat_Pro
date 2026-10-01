"""Patch footer PAGE fields with explicit format switches and strip empty pgNumType.

Maps each section's footerReference to its footer XML file via document.xml.rels,
then patches: upperRoman section -> PAGE \\* ROMAN, decimal section -> PAGE \\* arabic.
"""
import re
import shutil
import sys
import zipfile

DOCX = sys.argv[1]
TMP = DOCX + ".tmp"

with zipfile.ZipFile(DOCX) as z:
    names = z.namelist()
    files = {n: z.read(n) for n in names}

doc = files["word/document.xml"].decode("utf-8")
rels = files["word/_rels/document.xml.rels"].decode("utf-8")

# rId -> footer file target
rel_map = dict(re.findall(r'<Relationship[^>]*Id="([^"]+)"[^>]*Target="(footer\d+\.xml)"', rels))
rel_map.update(dict(
    (m.group(1), m.group(2))
    for m in re.finditer(r'<Relationship[^>]*Target="(footer\d+\.xml)"[^>]*Id="([^"]+)"', rels)
    if False  # placeholder; handled below
))
# also handle attribute order Target before Id
for m in re.finditer(r'<Relationship[^>]*Target="(footer\d+\.xml)"[^>]*Id="([^"]+)"', rels):
    rel_map[m.group(2)] = m.group(1)

# find sectPr blocks and their pgNumType fmt + footer rIds
sect_blocks = re.findall(r"<w:sectPr[^>]*>.*?</w:sectPr>", doc, re.S)
plan = {}  # footer file -> fmt keyword
for block in sect_blocks:
    fmt = None
    m = re.search(r'<w:pgNumType[^>]*w:fmt="([^"]+)"', block)
    if m:
        fmt = m.group(1)
    for rid in re.findall(r'<w:footerReference[^>]*r:id="([^"]+)"', block):
        target = rel_map.get(rid)
        if target and fmt:
            plan["word/" + target] = fmt

print("footer patch plan:", plan)

for fname, fmt in plan.items():
    if fname not in files:
        continue
    xml = files[fname].decode("utf-8")
    switch = "ROMAN" if "oman" in fmt or "OMAN" in fmt else "arabic"
    xml2 = re.sub(
        r"(<w:instrText[^>]*>)\s*PAGE\s*(</w:instrText>)",
        r"\1 PAGE \\* " + switch + r" \\* MERGEFORMAT \2",
        xml,
    )
    if xml2 != xml:
        files[fname] = xml2.encode("utf-8")
        print(f"patched {fname} -> \\* {switch}")

# strip empty pgNumType (no attributes) — confuses WPS
doc2 = re.sub(r"<w:pgNumType/>", "", doc)
if doc2 != doc:
    print("removed empty <w:pgNumType/>")
files["word/document.xml"] = doc2.encode("utf-8")

with zipfile.ZipFile(TMP, "w", zipfile.ZIP_DEFLATED) as z:
    for n in names:
        z.writestr(n, files[n])
shutil.move(TMP, DOCX)
print("done:", DOCX)
