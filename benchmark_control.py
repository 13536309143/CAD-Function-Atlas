"""Record an explicit stop before/after the operator terminates owned processes."""
import argparse
import json
from benchmark_store import transaction, now


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    with transaction(args.root) as db:
        db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', ('pipeline', json.dumps({'status': 'blocked', 'reason': 'Stopped by explicit user control command; resume requires an explicit run command.', 'at': now()})))
        db.execute("UPDATE requests SET state='uncertain' WHERE state='started'")
        db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)', (now(), 'user_stop', '{}'))
    print('Stop recorded; completed predictions and request budget remain preserved.')
