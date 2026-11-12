from pathlib import Path
import shutil
import subprocess
import pytest


def test_browsing_catalog_never_writes_the_shared_switch():
    node=shutil.which('node')
    if not node:pytest.skip('Node is needed for the browser behavior contract')
    subprocess.run([node,str(Path(__file__).with_name('local_view_contract.cjs'))],check=True,timeout=15)
