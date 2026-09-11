"""Package only runtime files, excluding tests, backups, caches and demo assets."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
files = ('__init__.py', 'geometry.py', 'setup.py', 'session.py', 'facial.py', 'operators.py', 'ui.py', 'corner_overlay.py', 'README.md')
output = root / 'artifacts' / 'curvemorph_4_0_0.zip'
output.parent.mkdir(exist_ok=True)
with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
    for filename in files:
        archive.write(root / filename, f'curvemorph/{filename}')
with ZipFile(output) as archive:
    assert archive.testzip() is None
    assert len(archive.namelist()) == len(files)
print(output)
