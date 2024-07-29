#!/usr/bin/env python3

import os
import arrow
import logging
import json
import time
import asyncio
import requests
import xml.etree.ElementTree as ET

from flask import Flask, Response, request, abort, redirect, jsonify

is_gunicorn = "gunicorn" in os.environ.get("SERVER_SOFTWARE", "")
if is_gunicorn:
    from prometheus_flask_exporter.multiprocess import GunicornInternalPrometheusMetrics as PrometheusMetrics
else:
    from prometheus_flask_exporter import PrometheusMetrics

from prometheus_client import Counter, Gauge

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)-8s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S')

logger = logging.getLogger(__name__)

app = Flask(__name__)

metrics = PrometheusMetrics(app)

MAX_ITEMS = 50
def proxy(path, max_items=MAX_ITEMS):
    r = requests.get(path)
    print('proxy(%s): %d' % (path, r.status_code))
    text = r.text
    root = ET.fromstring(text)

    item_count = 0
    i = 0
    while i < len(root[0]):
        if root[0][i].tag == 'item':
            item_count += 1
            if item_count >= max_items:
                del root[0][i]
            else:
                i += 1
        else:
            i += 1


    text = ET.tostring(root)

    return Response(text, mimetype='application/xml; charset=utf-8')

@app.route('/verge')
def verge():
    return proxy('https://www.theverge.com/rss/full.xml')

@app.route('/daily')
def daily():
    return proxy('http://feeds.simplecast.com/54nAGcIl')

@app.route('/healthz')
def healthz_route():
    return 'ok'