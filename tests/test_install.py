import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from install_local import register

class InstallerTests(unittest.TestCase):
    def test_preserve_idempotent_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);source=home/'Desktop/My plugins/plugin';source.mkdir(parents=True)
            (source/'plugin.json').write_text('{"name":"example"}')
            catalog=home/'.agents/plugins/marketplace.json';catalog.parent.mkdir(parents=True)
            catalog.write_text(json.dumps({'name':'existing','plugins':[{'name':'other','source':'./other'}],'custom':42}))
            register(source,home);register(source,home)
            data=json.loads(catalog.read_text())
            self.assertEqual(len(data['plugins']),2);self.assertEqual(data['custom'],42)
            self.assertEqual(data['name'],'existing')
            register(source,home,remove=True)
            self.assertEqual([x['name'] for x in json.loads(catalog.read_text())['plugins']],['other'])
    def test_conflict_no_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);source=home/'plugin';source.mkdir()
            (source/'plugin.json').write_text('{"name":"example"}')
            catalog=home/'.agents/plugins/marketplace.json';catalog.parent.mkdir(parents=True)
            text='{"plugins":[{"name":"example","source":"./other"}]}'
            catalog.write_text(text)
            with self.assertRaises(ValueError):register(source,home)
            self.assertEqual(catalog.read_text(),text)
