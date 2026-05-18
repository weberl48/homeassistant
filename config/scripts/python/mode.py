#!/usr/bin/env python3
"""WLED persistent ambient mode. Usage: mode.py <profile>
Sets an effect on both WLED controllers and returns. Mode stays on until changed.
Profiles: fireplace, aurora, ocean, candle, movie, lounge, police, disco,
           lightning, reading, warm_white, off."""
import sys, json, threading, urllib.request

WLED_IPS = ['192.168.1.105', '192.168.1.106']

MODES = {
    # Cozy / ambient
    'fireplace':   {'bri':200, 'fx':66, 'sx':100, 'ix':200, 'pal':35,
                    'col':[[255,80,0,0],[255,30,0,0],[255,200,80,0]]},
    'candle':      {'bri':90,  'fx':88, 'sx':60,  'ix':100, 'pal':0,
                    'col':[[255,120,30,180],[255,100,20,0],[255,50,0,0]]},
    'aurora':      {'bri':200, 'fx':38, 'sx':100, 'ix':200, 'pal':50,
                    'col':[[0,255,100,0],[100,0,200,0],[0,100,255,0]]},
    'ocean':       {'bri':200, 'fx':75, 'sx':100, 'ix':200, 'pal':9,
                    'col':[[0,100,200,0],[0,200,200,0],[0,50,150,0]]},
    'lounge':      {'bri':140, 'fx':110,'sx':40,  'ix':140, 'pal':47,
                    'col':[[255,150,30,80],[200,100,20,60],[100,50,10,40]]},
    # High-energy / novelty
    'police':      {'bri':255, 'fx':23, 'sx':200, 'ix':255, 'pal':0,
                    'col':[[255,0,0,0],[0,0,255,0],[0,0,0,0]]},
    'disco':       {'bri':220, 'fx':30, 'sx':240, 'ix':255, 'pal':6,
                    'col':[[255,0,255,0],[0,255,255,0],[255,255,0,0]]},
    'lightning':   {'bri':200, 'fx':57, 'sx':220, 'ix':220, 'pal':0,
                    'col':[[255,255,255,255],[100,100,255,0],[0,0,40,0]]},
    # Utility
    'movie':       {'bri':40,  'fx':0,  'sx':0,   'ix':0,   'pal':0,
                    'col':[[60,30,10,40],[0,0,0,0],[0,0,0,0]]},
    'reading':     {'bri':220, 'fx':0,  'sx':0,   'ix':0,   'pal':0,
                    'col':[[0,0,0,255],[0,0,0,0],[0,0,0,0]]},
    'warm_white':  {'bri':255, 'fx':0,  'sx':0,   'ix':0,   'pal':0,
                    'col':[[0,0,0,255],[0,0,0,0],[0,0,0,0]]},
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
    if len(sys.argv) < 2:
        sys.exit(f'usage: mode.py <off|{"|".join(MODES)}>')
    name = sys.argv[1]
    if name == 'off':
        fanout({'on': False, 'transition': 14})
        print('mode: off')
        return
    if name not in MODES:
        sys.exit(f'unknown mode: {name}')
    m = MODES[name]
    fanout({'on': True, 'bri': m['bri'], 'transition': 14,
            'seg': [{'id':0, 'fx':m['fx'], 'sx':m['sx'], 'ix':m['ix'], 'pal':m['pal'], 'col':m['col']}]})
    print(f'mode: {name}')


if __name__ == '__main__':
    main()
