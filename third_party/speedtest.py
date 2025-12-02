"""
Simplified, safe version of speedtest.py for GUI/PyInstaller usage.
This version removes:
- All CLI printing
- All stdout/stderr wrapping
- All __builtin__ imports
- All fileno() calls
Only the Speedtest class and supporting code remain.
"""

import math
import platform
import re
import socket
import sys
import threading
import timeit
import xml.parsers.expat
from urllib.request import (
    urlopen, Request, HTTPError, URLError,
)
from urllib.parse import urlparse, parse_qs
from http.client import HTTPConnection, HTTPSConnection, BadStatusLine
from io import BytesIO
import gzip

__version__ = "2.1.4-GUI"

# -----------------------------
# Utility stubs (no console UI)
# -----------------------------

def print_(*args, **kwargs):
    return  # suppress all printing for GUI apps

def to_utf8(v):
    return v

# -----------------------------
# Exceptions
# -----------------------------

class SpeedtestException(Exception):
    pass

class ConfigRetrievalError(SpeedtestException):
    pass

class ServersRetrievalError(SpeedtestException):
    pass

class SpeedtestServersError(SpeedtestException):
    pass

class SpeedtestBestServerFailure(SpeedtestException):
    pass

class NoMatchedServers(SpeedtestException):
    pass

# -----------------------------
# HTTP Utilities
# -----------------------------

def build_user_agent():
    ua = (
        f"Mozilla/5.0 (Python {platform.python_version()}) "
        f"speedtest-cli/{__version__}"
    )
    return ua

def build_request(url, data=None, headers=None):
    if headers is None:
        headers = {}
    headers["User-Agent"] = build_user_agent()
    return Request(url, data=data, headers=headers)

def catch_request(request):
    try:
        return urlopen(request), None
    except (HTTPError, URLError, socket.error, BadStatusLine) as e:
        return None, e

def get_response_stream(response):
    try:
        encoding = response.headers.get("Content-Encoding")
    except Exception:
        encoding = None

    if encoding == "gzip":
        return gzip.GzipFile(fileobj=BytesIO(response.read()))
    return response

# -----------------------------
# Distance
# -----------------------------

def distance(a, b):
    lat1, lon1 = a
    lat2, lon2 = b
    radius = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    aa = (
        math.sin(dlat / 2) ** 2 +
        math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(aa), math.sqrt(1 - aa))
    return radius * c

# -----------------------------
# Results container
# -----------------------------

class SpeedtestResults:
    def __init__(self):
        self.download = 0
        self.upload = 0
        self.ping = 0
        self.server = {}
        self.bytes_sent = 0
        self.bytes_received = 0

# -----------------------------
# Speedtest class
# -----------------------------

class Speedtest:
    def __init__(self, timeout=10, secure=False):
        self.timeout = timeout
        self.secure = secure
        self._opener = urlopen
        self.servers = {}
        self.closest = []
        self.results = SpeedtestResults()

        self.get_config()

    # -------------------------
    # Config
    # -------------------------

    def get_config(self):
        url = "https://www.speedtest.net/speedtest-config.php"
        request = build_request(url)
        resp, err = catch_request(request)
        if err:
            raise ConfigRetrievalError(err)

        data = resp.read()
        resp.close()

        # Extract lat/lon
        try:
            mlat = re.search(rb'lat="([^"]+)"', data).group(1)
            mlon = re.search(rb'lon="([^"]+)"', data).group(1)
            lat = float(mlat)
            lon = float(mlon)
            self.lat_lon = (lat, lon)
        except Exception as e:
            raise SpeedtestException("Could not parse config coordinates") from e

    # -------------------------
    # Servers
    # -------------------------

    def get_servers(self):
        urls = [
            "https://www.speedtest.net/speedtest-servers-static.php",
            "http://c.speedtest.net/speedtest-servers-static.php",
        ]

        errors = []
        for url in urls:
            req = build_request(url)
            resp, err = catch_request(req)
            if err:
                errors.append(err)
                continue

            stream = get_response_stream(resp)
            xml_data = stream.read()
            resp.close()

            # Parse manually (simple)
            servers = re.findall(
                rb'<server url="([^"]+)" lat="([^"]+)" lon="([^"]+)" name="([^"]+)" '
                rb'country="([^"]+)" cc="([^"]+)" sponsor="([^"]+)" id="([^"]+)"',
                xml_data
            )

            for (urlb, latb, lonb, name, country, cc, sponsor, sid) in servers:
                try:
                    lat = float(latb)
                    lon = float(lonb)
                    dist = distance(self.lat_lon, (lat, lon))
                    entry = {
                        "url": urlb.decode(),
                        "lat": lat,
                        "lon": lon,
                        "name": name.decode(),
                        "country": country.decode(),
                        "sponsor": sponsor.decode(),
                        "id": sid.decode(),
                        "d": dist,
                    }
                    self.servers.setdefault(dist, []).append(entry)
                except:
                    continue

            return self.servers

        raise ServersRetrievalError(errors)

    def get_closest_servers(self):
        if not self.servers:
            self.get_servers()

        for d in sorted(self.servers.keys()):
            for s in self.servers[d]:
                self.closest.append(s)
                if len(self.closest) >= 5:
                    return self.closest
        return self.closest

    def get_best_server(self):
        if not self.closest:
            self.get_closest_servers()

        results = {}
        for s in self.closest:
            url = s["url"].rsplit("/", 1)[0] + "/latency.txt"
            req = build_request(url)
            start = timeit.default_timer()
            resp, err = catch_request(req)
            if err:
                continue
            resp.read()
            t = (timeit.default_timer() - start) * 1000
            results[t] = s

        if not results:
            raise SpeedtestBestServerFailure("Could not find best server")

        best_latency = sorted(results.keys())[0]
        best = results[best_latency]
        best["latency"] = best_latency
        self.results.ping = best_latency
        self.results.server = best
        return best

    # -------------------------
    # Download
    # -------------------------

    def download(self):
        srv = self.get_best_server()
        base = srv["url"].rsplit("/", 1)[0]
        url = base + "/random4000x4000.jpg"

        req = build_request(url)
        start = timeit.default_timer()
        resp, err = catch_request(req)
        if err:
            return 0

        total = len(resp.read())
        duration = timeit.default_timer() - start

        self.results.bytes_received = total
        self.results.download = (total * 8) / duration  # bits/sec
        return self.results.download

    # -------------------------
    # Upload
    # -------------------------

    def upload(self):
        srv = self.results.server or self.get_best_server()
        url = srv["url"]

        data = b"x" * 1048576  # 1 MB
        req = build_request(url, data=data)

        start = timeit.default_timer()
        try:
            resp = urlopen(req)
            resp.read()
        except:
            return 0

        duration = timeit.default_timer() - start

        self.results.bytes_sent = len(data)
        self.results.upload = (len(data) * 8) / duration
        return self.results.upload
