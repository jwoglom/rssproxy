#!/usr/bin/env python3

import os
import arrow
import logging
import json
import time
import requests
from lxml import etree as ET
import re
import tempfile
import hashlib
import base64

from flask import Flask, Response, request, abort, redirect, jsonify

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)-8s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S')

logger = logging.getLogger(__name__)
tmp = os.path.join(tempfile.gettempdir(), 'rssproxy')
if not os.path.exists(tmp):
    os.makedirs(tmp)

BASE_URL = os.getenv('BASE_URL', '')

app = Flask(__name__)

MAX_ITEMS = 50
def proxy(path, max_items=MAX_ITEMS, mode=None, maxsize=None):
    logger.info('proxy(%s): start' % path)
    r = requests.get(path, timeout=10)
    logger.info('proxy(%s): fetched %d' % (path, r.status_code))

    text = ''
    logger.info('proxy(%s): mode %s' % (path, mode))
    if mode == 'lxml':
        text = r.text
        root = ET.fromstring(text.encode('utf-8'))
        logger.info('proxy(%s): parsed len=%d ln=%d' % (path, len(text), len(root[0])))

        item_count = 0
        i = 0
        ln = len(root[0])
        while i < ln:
            if root[0][i].tag == 'item':
                item_count += 1
                if item_count >= max_items:
                    del root[0][i]
                    ln = len(root[0])
                else:
                    root[0][i] = fixup_item(root[0][i], path)
                    i += 1
            else:
                i += 1


        text = ET.tostring(root)
    else:
        if mode == 'fast':
            maxsize = maxsize or 200000
            text = r.text[:maxsize]
            if text.count("</item>") >= max_items:
                def find_nth(s, x, n=0, overlap=False):
                    l = 1 if overlap else len(x)
                    i = -l
                    for c in range(n + 1):
                        i = s.find(x, i + l)
                        if i < 0:
                            break
                    return i
                index = find_nth(text, "</item>", max_items)
                if index:
                    text = text[:index] + "</channel></rss>"
        elif mode == 'fastest':
            i = 0
            maxsize = maxsize or 50*4096
            for chunk in r.iter_content(4096):
                text += chunk.decode('utf-8')
                i += 4096

                if i >= maxsize:
                    break

            text = text[:text.rindex("</item>")] + "</item></channel></rss>"

        logger.info('proxy(%s): fixup start' % path)
        print('text=', text)
        root = ET.fromstring(text.encode('utf-8'))
        for i in range(len(root[0])):
            try:
                root[0][i] = fixup_item(root[0][i], path)
            except IndexError:
                pass
        
        text = ET.tostring(root)

    logger.info('proxy(%s): done' % path)
    return Response(text, mimetype='application/xml; charset=utf-8')

def enc(x):
    return base64.b64encode(x.encode() if x else b'', b'-_').decode()

def dec(x):
    return base64.b64decode(x.encode() if x else b'', b'-_').decode()

def url_for_proxy(url, proxy_path):
    pp = enc(proxy_path)
    fp = os.path.join(tmp, pp)
    en = enc(url)
    if not os.path.exists(fp):
        with open(fp, 'w') as f:
            f.write('')
    with open(fp, 'r+') as f:
        existing = set([i.strip() for i in f.readlines()])
        if en not in existing:
            logger.info('allowing url_for_proxy %s' % url)
            f.write('%s\n' % en)
    
    return '%s/proxy?pp=%s&en=%s' % (BASE_URL, pp, en)

def can_proxy_url(en, pp):
    fp = os.path.join(tmp, pp)
    if not os.path.exists(fp):
        return False
    with open(fp, 'r') as f:
        existing = set([i.strip() for i in f.readlines()])
        return en in existing
    

def fixup_item(item, proxy_path):
    for i, it in enumerate(list(item)):
        if it.tag == 'enclosure':
            if 'url' in it.attrib:
                item[i].attrib['url'] = url_for_proxy(it.attrib['url'], proxy_path)

        if it.tag.endswith('thumbnail'):
            if 'url' in it.attrib:
                item[i].attrib['url'] = url_for_proxy(it.attrib['url'], proxy_path)
    
    return item


@app.route('/verge')
def verge():
    return proxy('https://www.theverge.com/rss/full.xml', mode='lxml', max_items=request.args.get('items', None))

@app.route('/daily')
def daily():
    return proxy('https://feeds.simplecast.com/54nAGcIl', mode='fastest', maxsize=request.args.get('maxsize', None))

def build_proxy_resp(url, request_headers):
    send_headers = dict(request_headers)
    del send_headers['Host']
    r = requests.get(url, stream=True, headers=send_headers, allow_redirects=False)
    headers = dict(r.raw.headers)
    def generate():
        for chunk in r.raw.stream(decode_content=False):
            yield chunk
    out = Response(generate(), headers=headers)
    out.status_code = r.status_code
    return out

@app.route('/proxy')
def proxy_route():
    pp = request.args.get('pp')
    furl = dec(pp)
    en = request.args.get('en')
    url = dec(en)

    if can_proxy_url(en, pp):
        logger.info('proxy_route(%s): fetching %s' % (furl, url))
        out = build_proxy_resp(url, request.headers)
        orig_out = out
        while out and 'location' in set(i.lower() for i in out.headers.keys()):
            newh = dict(request.headers)
            logger.info('new referrer %s %s' % (url, json.dumps(newh)))
            newh['Referer'] = url
            url = out.headers.get('Location', out.headers.get('location'))
            if not url:
                return orig_out
            out = build_proxy_resp(url, newh)
        if not out:
            out = orig_out
        return out
    else:
        logger.info('proxy_route(%s): failed %s' % (furl, url))
        abort(403, 'cannot proxy %s via %s (en=%s pp=%s)' % (url, furl, en, pp))



@app.route('/healthz')
def healthz_route():
    return 'ok'