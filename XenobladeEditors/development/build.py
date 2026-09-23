"""Build without modifying the user's global Python installation."""
from pathlib import Path
import subprocess
import sys
import os

ROOT=Path(__file__).resolve().parent.parent
tools=ROOT/'development/build-tools'
env=dict(os.environ,PYTHONPATH=str(tools))
args=[sys.executable,'-m','PyInstaller','--noconfirm','--windowed','--onedir',
      '--name','XenobladeEditors','--icon',str(ROOT/'resources/artwork/monado.ico'),
      '--distpath',str(ROOT/'development/dist'),'--workpath',str(ROOT/'development/build'),
      '--specpath',str(ROOT/'development'),'--paths',str(ROOT/'source'),
      '--exclude-module','numpy','--exclude-module','matplotlib','--exclude-module','IPython',
      str(ROOT/'source/launcher.py')]
subprocess.run(args,env=env,check=True)
print('Build complete: development/dist/XenobladeEditors. Install this directory as app/.')
