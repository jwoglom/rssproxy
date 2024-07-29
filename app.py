#!/usr/bin/env python3

import os
import arrow
import logging
import json
import time
import requests
from lxml import etree as ET
import re

from flask import Flask, Response, request, abort, redirect, jsonify

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)-8s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S')

logger = logging.getLogger(__name__)

app = Flask(__name__)

MAX_ITEMS = 50
def proxy(path, max_items=MAX_ITEMS, mode=None):
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
                    i += 1
            else:
                i += 1


        text = ET.tostring(root)
    elif mode == 'fast':
        text = r.text[:200000]
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
        for chunk in r.iter_content(4096):
            text += chunk.encode('utf-8')
            i += 4096

            if i >= 50*1024:
                break

        text = text[:text.rindex("</item>")] + "</channel></rss>"

    logger.info('proxy(%s): done' % path)
    return Response(text, mimetype='application/xml; charset=utf-8')

@app.route('/verge')
def verge():
    return proxy('https://www.theverge.com/rss/full.xml', mode='lxml')

@app.route('/daily')
def daily():
    return proxy('https://feeds.simplecast.com/54nAGcIl', mode='fastest')

@app.route('/healthz')
def healthz_route():
    return 'ok'