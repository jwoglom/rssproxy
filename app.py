#!/usr/bin/env python3

import os
import arrow
import logging
import json
import time
import requests
import xml.etree.ElementTree as ET

from flask import Flask, Response, request, abort, redirect, jsonify

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)-8s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S')

logger = logging.getLogger(__name__)

app = Flask(__name__)

MAX_ITEMS = 50
def proxy(path, max_items=MAX_ITEMS):
    logger.info('fetching %s' % path)
    r = requests.get(path, timeout=10)
    logger.info('proxy(%s): %d' % (path, r.status_code))
    text = r.text
    root = ET.fromstring(text)

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
    logger.info('proxy(%s): done')

    return Response(text, mimetype='application/xml; charset=utf-8')

@app.route('/verge')
def verge():
    return proxy('https://www.theverge.com/rss/full.xml')

@app.route('/daily')
def daily():
    return proxy('https://feeds.simplecast.com/54nAGcIl')

@app.route('/healthz')
def healthz_route():
    return 'ok'