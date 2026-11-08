"""Inventory supplied reference documents without claiming PDF/content verification."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile


def main():
    path = Path('data/imports/cboe-historical-calendars-and-listing-records.zip')
    records = []
    years = []
    with ZipFile(path) as archive:
        for manifest in ('calendar-manifest.json', 'reference-manifest.json'):
            entries = json.loads(archive.read(manifest))
            for entry in entries:
                record = dict(entry)
                record['manifest'] = manifest
                if entry['file'] in archive.namelist():
                    data = archive.read(entry['file'])
                    if 'bytes' in entry and entry['bytes'] != len(data):
                        raise ValueError(f"Size mismatch: {entry['file']}")
                    if entry['file'].endswith('.pdf') and not data.startswith(b'%PDF-'):
                        raise ValueError(f"Invalid PDF signature: {entry['file']}")
                    record.update(available=True, sha256=hashlib.sha256(data).hexdigest())
                    if manifest == 'calendar-manifest.json':
                        years.append(entry['year'])
                else:
                    record['available'] = False
                records.append(record)
    receipt = dict(archive=str(path), archive_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                   calendar_years=sorted(years), documents=records,
                   limitation='Manifest, byte counts and signatures checked; PDF contents not parsed. '
                              'Current snapshots and rule notices are not complete historical eligibility records.')
    output = Path('artifacts/reference_archive_inventory.json')
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(f'Calendar years: {sorted(years)}; available documents: '
          f'{sum(record["available"] for record in records)}/{len(records)}')


if __name__ == '__main__':
    main()
