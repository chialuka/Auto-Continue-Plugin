"""Register this folder in the desktop's personal plugin marketplace. No CLI required."""
import argparse
import datetime
import json
from pathlib import Path
import shutil
import tempfile


def register(source, home, remove=False):
    source=Path(source).resolve();home=Path(home).resolve()
    name=json.loads((source/'plugin.json').read_text())['name']
    relative=source.relative_to(home)  # The personal marketplace cannot reference outside its root.
    path=home/'.agents/plugins/marketplace.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    catalog=json.loads(path.read_text()) if path.exists() else {
        'name':'local-codex-plugins','interface':{'displayName':'Local Codex Plugins'},'plugins':[]}
    if not isinstance(catalog,dict) or not isinstance(catalog.get('plugins'),list):
        raise ValueError('Existing marketplace has an unsupported format; no changes made')
    entries=[entry for entry in catalog['plugins'] if entry.get('name')==name]
    target={'source':'local','path':'./'+relative.as_posix()}
    if entries and any(entry.get('source')!=target for entry in entries):
        raise ValueError('A different source already uses this plugin name; no changes made')
    if remove:
        catalog['plugins']=[entry for entry in catalog['plugins'] if entry.get('name')!=name]
    elif not entries:
        catalog['plugins'].append({'name':name,'source':target,
            'policy':{'installation':'AVAILABLE','authentication':'ON_INSTALL'},'category':'Productivity'})
    if path.exists():
        backup=path.with_name('marketplace.backup-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.json')
        shutil.copy2(path,backup)
    with tempfile.NamedTemporaryFile('w',dir=path.parent,delete=False,encoding='utf-8') as f:
        json.dump(catalog,f,indent=2);f.write('\n');temp=Path(f.name)
    temp.replace(path)
    return path


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remove',action='store_true',help='Remove marketplace entry, retaining all data and plugin files')
    args=parser.parse_args()
    print(register(Path(__file__).resolve().parents[1],Path.home(),args.remove))
    print('Restart the desktop app, open Plugins, select the local source, and install or refresh Resume After Reset.')
