"""Tiny synthetic catalogs for the unit tests. No real account data."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

TEMPLATE = ('{"name":"Microsoft.XboxLive.T7.REPLACEEVENTNAME","time":"REPLACETIME","seq":REPLACESEQ,'
            '"data":{"baseData":{"name":"REPLACEEVENTNAME","titleId":7,"serviceConfigId":"00000000-0000-0000-0000-000000000007",'
            '"userId":"REPLACEXUID","playerSessionId":"REPLACESESSIONGUID","properties":{"AchievementId":REPLACEID}}}}\n')


def op(target, value):
    return {'ReplacementType': 'Replace', 'Target': target, 'Replacement': value}


def block(**extra):
    value = {'TitleId': 7, 'TitleName': 'Seven', 'Enabled': True, 'FullySupported': True,
             'Achievements': {'1': {'Request1': {'E': op('REPLACEEVENTNAME', 'Unlock'), 'I': op('REPLACEID', '1')}},
                              '2': {'E': op('REPLACEEVENTNAME', 'Unlock'), 'I': op('REPLACEID', 2)}}}
    value.update(extra)
    return value


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def write_catalog(titles, title=None, template=TEMPLATE, stats=None):
    title = block() if title is None else title
    write_json(titles / '7' / 'Data.json', {'7': title, 'SupportedTitleIDs': ['7']})
    (titles / '7' / '7.json').write_bytes(template.encode())
    write_json(titles / 'index.json', {'7': {k: v for k, v in title.items() if k != 'Achievements'}, 'SupportedTitleIDs': ['7']})
    if stats is not None:
        write_json(titles / '7' / 'Stats.json', {'7': stats, 'SupportedTitleIDs': ['7']})
