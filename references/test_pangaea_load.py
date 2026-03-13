import sys
from pangaeapy.pandataset import PanDataSet

DOI = 'https://doi.pangaea.de/10.1594/PANGAEA.946025'
print('Loading', DOI)
try:
    ds = PanDataSet(DOI)
    print('Loaded title:', ds.title)
    print('Events count:', len(getattr(ds, 'events', [])))
    print('Columns head:', list(ds.data.columns)[:10])
    print('Rows:', len(ds.data))
    print('Done')
except Exception as e:
    print('ERROR:', repr(e))
    sys.exit(2)
