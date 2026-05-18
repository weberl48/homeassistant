#!/usr/bin/env python3
"""WLED multi-phase celebration. Usage: celebrate.py <profile>
Profiles: sabres, bills, party, christmas, halloween, july4, birthday, penalty, new_year.
Each profile is a sequence of phases that play in order, then lights fade off."""
import sys, json, time, threading, urllib.request

WLED_IPS = ['192.168.1.105', '192.168.1.106']

# Phase tuple: (fx, sx, ix, duration_seconds, palette_id=0)
PROFILES = {
    'sabres': {
        'colors': [[252, 181, 20, 0], [0, 38, 84, 0], [255, 255, 255, 255]],
        'phases': [(23, 255, 255, 4), (89, 200, 220, 25), (13, 180, 200, 5)],
    },
    'bills': {
        'colors': [[198, 12, 48, 0], [0, 51, 141, 0], [255, 255, 255, 255]],
        'phases': [(23, 255, 255, 4), (89, 200, 220, 25), (13, 180, 200, 5)],
    },
    'party': {
        'colors': [[255, 0, 0, 0], [0, 255, 0, 0], [0, 0, 255, 0]],
        'phases': [(9, 220, 255, 5), (89, 255, 255, 20, 6), (42, 220, 255, 5)],
    },
    'christmas': {
        'colors': [[255, 0, 0, 0], [0, 200, 0, 0], [255, 255, 255, 200]],
        'phases': [(17, 180, 220, 5), (89, 200, 220, 20), (13, 150, 200, 5)],
    },
    'halloween': {
        'colors': [[255, 80, 0, 0], [128, 0, 200, 0], [50, 0, 100, 0]],
        'phases': [(66, 100, 200, 8, 35), (82, 200, 220, 12), (57, 220, 220, 5), (42, 200, 240, 5)],
    },
    'july4': {
        'colors': [[255, 0, 0, 0], [255, 255, 255, 255], [0, 0, 255, 0]],
        'phases': [(23, 255, 255, 3), (89, 200, 240, 18), (42, 220, 240, 10), (13, 180, 200, 4)],
    },
    'birthday': {
        'colors': [[255, 0, 255, 0], [0, 255, 255, 0], [255, 255, 0, 0]],
        'phases': [(9, 240, 255, 4), (89, 240, 255, 18, 6), (87, 220, 220, 8)],
    },
    'penalty': {
        # Sad pulse — opponent scored against us
        'colors': [[180, 0, 0, 0], [80, 0, 0, 0], [0, 0, 0, 0]],
        'phases': [(2, 60, 150, 8)],
    },
    'new_year': {
        'colors': [[255, 215, 0, 0], [255, 255, 255, 255], [0, 100, 255, 0]],
        'phases': [(8, 180, 220, 10), (89, 255, 255, 25, 6), (87, 240, 255, 8)],
    },
}


def send(ip, body):
    try:
        req = urllib.request.Request(f'http://{ip}/json/state', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'}, method='POST')
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f'  {ip}: {e}', file=sys.stderr)


def fanout(body):
    threads = [threading.Thread(target=send, args=(ip, body)) for ip in WLED_IPS]
    for t in threads: t.start()
    for t in threads: t.join()


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in PROFILES:
        sys.exit(f'usage: celebrate.py <{"|".join(PROFILES)}>')
    profile = PROFILES[sys.argv[1]]
    for phase in profile['phases']:
        fx, sx, ix, dur = phase[:4]
        pal = phase[4] if len(phase) > 4 else 0
        fanout({'on': True, 'bri': 255, 'transition': 7,
                'seg': [{'id': 0, 'fx': fx, 'sx': sx, 'ix': ix, 'pal': pal, 'col': profile['colors']}]})
        time.sleep(dur)
    fanout({'on': False, 'transition': 14})
    print(f'celebration "{sys.argv[1]}" done')


if __name__ == '__main__':
    main()
