"""Build a native executable and distributable archive on the current host."""
import hashlib
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from zhongce_radar import __version__

def main():
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--console',
                    '--name','radar','--paths',str(ROOT/'src'),'--collect-all','aiohttp',
                    '--distpath',str(ROOT/'dist'),'--workpath',str(ROOT/'build'),
                    '--specpath',str(ROOT/'build'),str(ROOT/'scripts/frozen_entry.py')],check=True,cwd=ROOT)
    executable=ROOT/'dist'/('radar.exe' if sys.platform=='win32' else 'radar')
    subprocess.run([str(executable),'--version'],check=True)
    name=f'zhongce-radar-{__version__}-{sys.platform}-{platform.machine().lower()}'
    folder=ROOT/'dist'/name
    folder.mkdir(exist_ok=True)
    for source in [executable,ROOT/'LICENSE',ROOT/'README.md']:
        shutil.copy2(source,folder/source.name)
    form='zip' if sys.platform=='win32' else 'gztar'
    artifact=Path(shutil.make_archive(str(ROOT/'dist'/name),form,root_dir=folder))
    artifact.with_name(artifact.name+'.sha256').write_text(hashlib.sha256(artifact.read_bytes()).hexdigest()+'  '+artifact.name+'\n',encoding='utf-8')
    print(artifact)

if __name__=='__main__': main()
