#!/usr/bin/env python3

import os
import arrow
import logging
import json
import time
import requests
from lxml import etree as ET
from lxml.builder import ElementMaker as EM
from lxml.builder import E as EB
import re
import tempfile
import hashlib
import base64
from urllib.parse import urlparse
from slugify import slugify

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
FEEDS = json.loads(os.getenv('FEEDS', '''{
    "verge": {
        "url": "https://www.theverge.com/rss/full.xml"
    },
    "ars": {
        "url": "https://feeds.arstechnica.com/arstechnica/index"
    },
    "daily": {
        "url": "https://feeds.simplecast.com/54nAGcIl",
        "mode": "fastest"
    },
    "vox": {
        "url": "https://www.vox.com/rss/index.xml"
    }
}'''))

app = Flask(__name__)

MAX_ITEMS = 50
def proxy(path, max_items=None, mode=None, maxsize=None):
    if max_items is None:
        max_items = MAX_ITEMS
    if mode is None:
        mode = 'lxml'

    logger.info('proxy(%s): start' % path)
    r = requests.get(path, timeout=10)
    logger.info('proxy(%s): fetched %d' % (path, r.status_code))

    text = ''
    logger.info('proxy(%s): mode %s' % (path, mode))
    if mode == 'lxml':
        text = r.text
        root = ET.fromstring(text.encode('utf-8'))
        logger.info('proxy(%s): parsed len=%d ln=%d' % (path, len(text), len(root[0])))

        print('root', root)

        if root.tag.endswith('feed'):
            root = atom_to_rss(root)

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
                    fixup_item(root[0][i], path)
                    i += 1
            else:
                i += 1

        fixup_item(root[0], path)

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
        root = ET.fromstring(text.encode('utf-8'))
        if root.tag.endswith('feed'):
            root = atom_to_rss(root)

        ln = len(root[0])
        
        for i in range(len(root[0])):
            try:
                fixup_item(root[0][i], path)
            except IndexError:
                pass

        fixup_item(root[0], path)

        text = ET.tostring(root)

    logger.info('proxy(%s): done' % path)
    return Response(text, mimetype='application/xml; charset=utf-8')


def ft(r, tag, orelse=''):
    if r is None:
        return orelse
    a = r.find(ATOM + tag)
    if a is not None and a.text:
        return a.text.strip()
    b = r.find(tag)
    if b is not None and b.text:
        return b.text.strip()
    return orelse

def fat(r, tag, orelse=None):
    if r is None:
        return orelse
    a = r.find(ATOM + tag)
    if a is not None:
        return a.attrib
    b = r.find(tag)
    if b is not None:
        return b.attrib
    return orelse

def f(r, tag, orelse=None):
    if r is None:
        return orelse
    a = r.find(ATOM + tag)
    if a is not None:
        return a
    b = r.find(tag)
    if b is not None:
        return b
    return orelse

ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
DC = "{http://purl.org/dc/elements/1.1/}"
def atom_to_rss(root):
    E = EM(nsmap={
        "atom": ATOM[1:-1], 
        "content": CONTENT[1:-1],
        "dc": DC[1:-1]
    })
    atom = EM(namespace=ATOM[1:-1])
    content = EM(namespace=CONTENT[1:-1])
    dc = EM(namespace=DC[1:-1])
    
    groot = E("rss", version="2.0")
    channel = E("channel")
    
    channel.append(E("title", ft(root, "title")))
    channel.append(E("link", ft(root, "link", orelse="")))
    channel.append(E("description", ft(root, "subtitle", orelse="")))
    channel.append(E("lastBuildDate", ft(root, "updated", orelse="")))
    channel.append(E("language", root.get("{http://www.w3.org/XML/1998/namespace}lang", "")))

    for entry in root.findall(ATOM + "entry"):
        item = E("item")
        item.append(E("title", ft(entry, "title")))
        item.append(E("link", ft(entry, "id", orelse=fat(entry, "link", {}).get('href', ''))))
        item.append(E("pubDate", arrow.get(ft(entry, "published")).format() if ft(entry, "published") else ''))
        item.append(E("description", ft(entry, "summary", orelse='')))
        item.append(E("author", ft(f(entry, "author"), "name", orelse='')))
        item.append(content("encoded", ft(entry, "content", orelse='')))

        channel.append(item)
    
    groot.append(channel)
    return groot

def enc(x):
    return base64.b64encode(x.encode() if x else b'', b'-_').decode()

def dec(x):
    return base64.b64decode(x.encode() if x else b'', b'-_').decode()

def url_for_proxy(url, proxy_path, title):
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
    
    ext = ''
    urlpath = urlparse(url).path
    if '.' in urlpath and (urlpath.rindex('.') or 0) >= (urlpath.rindex('/') or 0):
        ext = urlpath[urlpath.rindex('.'):]
    if title:
        ext = '_%s%s' % (slugify(title, separator='_'), ext)
    return '%s/proxy%s?pp=%s&en=%s' % (BASE_URL, ext, pp, en)

def can_proxy_url(en, pp):
    fp = os.path.join(tmp, pp)
    if not os.path.exists(fp):
        return False
    with open(fp, 'r') as f:
        existing = set([i.strip() for i in f.readlines()])
        return en in existing
    

def fixup_item(item, proxy_path):
    title = None
    for i, it in enumerate(list(item)):
        if it.tag == 'title' or it.tag.endswith('}title'):
            title = it.text

    for i, it in enumerate(list(item)):
        if it.tag == 'enclosure':
            if 'url' in it.attrib:
                item[i].attrib['url'] = url_for_proxy(it.attrib['url'], proxy_path, title)

        if it.tag == 'thumbnail' or it.tag.endswith('thumbnail'):
            if 'url' in it.attrib:
                item[i].attrib['url'] = url_for_proxy(it.attrib['url'], proxy_path, title)

        if it.tag == 'image' or it.tag.endswith('}image'):
            if 'href' in it.attrib:
                item[i].attrib['href'] = url_for_proxy(it.attrib['href'], proxy_path, title)

            for j, jt in enumerate(list(item[i])):
                if jt.tag == 'url' or jt.tag.endswith('}url'):
                    item[i][j].text = url_for_proxy(item[i][j].text, proxy_path, title)

        
        if it.tag == 'encoded' or it.tag.endswith('}encoded') or it.tag == 'content' or it.tag.endswith('}content'):
            try:
                html_root = ET.Element("root")
                html_root.append(ET.fromstring(it.text, parser=ET.HTMLParser()))
                
                ok = False
                imgs = html_root.findall('.//img')
                for img in imgs:
                    if 'src' in img.attrib:
                        img.attrib['src'] = url_for_proxy(img.attrib['src'], proxy_path, title)
                        ok = True

                if ok:
                    it.text = ''.join(ET.tostring(e, encoding='unicode') for e in html_root)
                    it.text = it.text.replace('<html><body>', '').replace('</html></body>', '')
            except ET.ParseError as e:
                pass



@app.route('/<path:feed>')
def feed_route(feed):
    if feed.endswith('.xml'):
        feed = feed[:-4]
    if feed.endswith('.rss'):
        feed = feed[:-4]

    if feed not in FEEDS.keys():
        return abort(404, 'invalid feed: %s, expected one of: %s' % (feed, ','.join(FEEDS.keys())))
    
    url = FEEDS[feed]['url']

    return proxy(url, mode=FEEDS[feed].get('mode', 'lxml'), 
                 max_items=request.args.get('items', FEEDS[feed].get('items')), 
                 maxsize=request.args.get('maxsize', FEEDS[feed].get('maxsize')))

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
@app.route('/proxy.<path:ext>')
@app.route('/proxy_<path:ign>.<path:ext>')
def proxy_route(ign=None, ext=None):
    pp = request.args.get('pp')
    furl = dec(pp)
    en = request.args.get('en', request.args.get('amp;en'))
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