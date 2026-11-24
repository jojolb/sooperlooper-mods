#!/usr/bin/env python3
"""Small HTTP/OSC bridge for controlling SooperLooper from a browser."""

import argparse
import json
import math
import os
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


MAX_LOOPS = 4
MAX_BODY = 8192
LOOP_AUTO_CONTROLS = (
    "state", "next_state", "loop_pos", "loop_len", "cycle_len",
    "waiting", "rate_output", "in_peak_meter", "out_peak_meter",
)
LOOP_UPDATE_CONTROLS = (
    "feedback", "rec_thresh", "wet", "dry", "input_gain", "rate",
    "sync", "playback_sync", "quantize", "round", "relative_sync", "is_soloed",
)
GLOBAL_UPDATE_CONTROLS = ("tempo", "sync_source", "eighth_per_cycle", "wet", "dry", "input_gain")
GLOBAL_AUTO_CONTROLS = ("in_peak_meter", "out_peak_meter", "global_cycle_len", "global_cycle_pos")
COMMANDS = {
    "record", "overdub", "multiply", "insert", "replace", "reverse",
    "mute", "undo", "redo", "oneshot", "trigger", "substitute",
    "undo_all", "redo_all", "solo", "pause",
}
LOOP_SET_CONTROLS = {
    "feedback", "rec_thresh", "wet", "dry", "input_gain", "rate",
    "sync", "playback_sync", "quantize", "round", "relative_sync",
}
GLOBAL_SET_CONTROLS = {"tempo", "wet", "dry", "input_gain", "sync_source"}
STATE_NAMES = {
    -1: "inconnu", 0: "arrêt", 1: "démarrage", 2: "enregistrement",
    3: "arrêt en attente", 4: "lecture", 5: "overdub", 6: "multiplication",
    7: "insertion", 8: "remplacement", 9: "délai", 10: "muet",
    11: "scratch", 12: "one-shot", 13: "substitution", 14: "pause",
}


def _osc_string(value):
    data = value.encode("utf-8") + b"\0"
    return data + b"\0" * ((-len(data)) % 4)


def encode_osc(path, arguments):
    """Encode arguments as (type, value) pairs."""
    tags = "," + "".join(kind for kind, _ in arguments)
    payload = bytearray(_osc_string(path) + _osc_string(tags))
    for kind, value in arguments:
        if kind == "s":
            payload.extend(_osc_string(str(value)))
        elif kind == "i":
            payload.extend(struct.pack(">i", int(value)))
        elif kind == "f":
            payload.extend(struct.pack(">f", float(value)))
        else:
            raise ValueError("OSC type not supported: " + kind)
    return bytes(payload)


def _read_osc_string(packet, offset):
    end = packet.find(b"\0", offset)
    if end < 0:
        raise ValueError("invalid OSC string")
    value = packet[offset:end].decode("utf-8", errors="replace")
    return value, (end + 4) & ~3


def decode_osc(packet):
    """Decode a simple OSC message. Bundles are not needed by SooperLooper."""
    if packet.startswith(b"#bundle"):
        raise ValueError("OSC bundles are not supported")
    path, offset = _read_osc_string(packet, 0)
    tags, offset = _read_osc_string(packet, offset)
    if not tags.startswith(","):
        raise ValueError("invalid OSC type tag string")
    values = []
    for kind in tags[1:]:
        if kind == "s":
            value, offset = _read_osc_string(packet, offset)
        elif kind in ("i", "f"):
            if offset + 4 > len(packet):
                raise ValueError("truncated OSC message")
            value = struct.unpack(">i" if kind == "i" else ">f", packet[offset:offset + 4])[0]
            offset += 4
        else:
            raise ValueError("unsupported OSC type: " + kind)
        values.append(value)
    return path, values


class SlWeb:
    def __init__(self, engine_host, engine_port, callback_host, callback_port):
        self.engine_host = engine_host
        self.engine_port = engine_port
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.state = {
            "connected": False,
            "engine_host": engine_host,
            "engine_port": engine_port,
            "engine_version": "",
            "loop_count": 0,
            "globals": {},
            "loops": [],
            "error": "",
        }
        self.loop_values = {}
        self.last_ping = 0.0
        self.connected_once = False
        self.registered_count = -1
        self.engine_id = None
        self.send_lock = threading.Lock()
        self.loop_lock = threading.Lock()
        self.pending_loop_adds = 0
        self.pending_loop_removes = 0

        self.osc_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.osc_socket.bind((callback_host, callback_port))
        self.osc_socket.settimeout(0.5)
        self.callback_port = self.osc_socket.getsockname()[1]
        self.callback_host = self._route_address(engine_host, engine_port)
        self.callback_url = "osc.udp://{}:{}/".format(self.callback_host, self.callback_port)
        self.engine_address = (engine_host, engine_port)
        self.engine_url = ""

    @staticmethod
    def _route_address(host, port):
        if host in ("localhost", "127.0.0.1"):
            return "127.0.0.1"
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((host, port))
            return probe.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            probe.close()

    def start(self):
        threading.Thread(target=self._osc_loop, daemon=True, name="slweb-osc").start()
        threading.Thread(target=self._ping_loop, daemon=True, name="slweb-ping").start()

    def close(self):
        self.stop_event.set()
        self.osc_socket.close()

    def send(self, path, *arguments):
        packet = encode_osc(path, arguments)
        with self.send_lock:
            self.osc_socket.sendto(packet, self.engine_address)

    def _ping_loop(self):
        while not self.stop_event.is_set():
            try:
                self.send("/ping", ("s", self.callback_url), ("s", "/pingack"), ("i", 1))
            except OSError as error:
                with self.lock:
                    self.state["error"] = str(error)
            with self.lock:
                was_connected = self.state["connected"]
                is_connected = self.last_ping and time.monotonic() - self.last_ping < 6
                self.state["connected"] = bool(is_connected)
                if not is_connected and self.connected_once:
                    self.state["error"] = "Connexion au moteur perdue"
                if was_connected and not is_connected:
                    self.registered_count = -1
            self.stop_event.wait(2)

    def _osc_loop(self):
        while not self.stop_event.is_set():
            try:
                packet, _ = self.osc_socket.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                path, values = decode_osc(packet)
                self._handle_osc(path, values)
            except (ValueError, IndexError, TypeError):
                continue

    def _handle_osc(self, path, values):
        if path == "/pingack" and len(values) >= 3:
            self._handle_pingack(values)
        elif path == "/ctrl" and len(values) >= 3:
            index, control, value = values[:3]
            with self.lock:
                if index == -2:
                    self.state["globals"][control] = value
                elif 0 <= index < MAX_LOOPS:
                    self.loop_values.setdefault(index, {})[control] = value
        elif path == "/prop" and len(values) >= 3:
            index, prop, value = values[:3]
            with self.lock:
                if 0 <= index < MAX_LOOPS and prop == "name":
                    self.loop_values.setdefault(index, {})["name"] = value
        elif path == "/error" and values:
            with self.lock:
                self.state["error"] = str(values[-1])

    def _handle_pingack(self, values):
        engine_url, version, count = values[:3]
        engine_id = values[3] if len(values) > 3 else None
        try:
            parsed = urlparse(engine_url)
            host = parsed.hostname
            port = parsed.port
            if host and port:
                self.engine_address = (host, port)
                self.engine_url = engine_url
        except ValueError:
            pass
        count = max(0, int(count))
        with self.lock:
            previous_count = self.state["loop_count"]
            self.last_ping = time.monotonic()
            self.connected_once = True
            self.state["connected"] = True
            self.state["engine_version"] = str(version)
            self.state["engine_host"] = self.engine_address[0]
            self.state["engine_port"] = self.engine_address[1]
            self.state["loop_count"] = count
            self.state["error"] = ""
        if engine_id is not None and self.engine_id is not None and engine_id != self.engine_id:
            self.registered_count = -1
            self.loop_values.clear()
        if engine_id is not None:
            self.engine_id = engine_id
        if count < previous_count:
            for index in range(count, previous_count):
                self.loop_values.pop(index, None)
        elif count > previous_count:
            for index in range(previous_count, count):
                self.loop_values.pop(index, None)
        with self.loop_lock:
            delta = count - previous_count
            if delta > 0:
                self.pending_loop_adds = max(0, self.pending_loop_adds - delta)
            elif delta < 0:
                self.pending_loop_removes = max(0, self.pending_loop_removes + delta)
        if self.registered_count != count:
            self._register(count)
            self.registered_count = count

    def _register(self, count):
        for control in GLOBAL_UPDATE_CONTROLS:
            self.send("/register_update", ("s", control), ("s", self.callback_url), ("s", "/ctrl"))
            self.send("/get", ("s", control), ("s", self.callback_url), ("s", "/ctrl"))
        for control in GLOBAL_AUTO_CONTROLS:
            self.send("/register_auto_update", ("s", control), ("i", 100),
                      ("s", self.callback_url), ("s", "/ctrl"))
        for index in range(min(count, MAX_LOOPS)):
            prefix = "/sl/{}/".format(index)
            for control in LOOP_UPDATE_CONTROLS:
                self.send(prefix + "register_update", ("s", control), ("s", self.callback_url), ("s", "/ctrl"))
                self.send(prefix + "get", ("s", control), ("s", self.callback_url), ("s", "/ctrl"))
            for control in LOOP_AUTO_CONTROLS:
                self.send(prefix + "register_auto_update", ("s", control), ("i", 100),
                          ("s", self.callback_url), ("s", "/ctrl"))
            for control in LOOP_AUTO_CONTROLS:
                self.send(prefix + "get", ("s", control), ("s", self.callback_url), ("s", "/ctrl"))
            self.send(prefix + "get_prop", ("s", "name"), ("s", self.callback_url), ("s", "/prop"))

    def snapshot(self):
        with self.lock:
            count = self.state["loop_count"]
            loops = []
            for index in range(min(count, MAX_LOOPS)):
                values = dict(self.loop_values.get(index, {}))
                state_value = int(values.get("state", -1))
                values["state_label"] = STATE_NAMES.get(state_value, "état {}".format(state_value))
                values["index"] = index
                loops.append(values)
            result = dict(self.state)
            result["loops"] = loops
            result["hidden_loops"] = max(0, count - MAX_LOOPS)
            result["globals"] = dict(self.state["globals"])
            return result

    def command(self, index, command, phase):
        index = int(index)
        if not self.state["connected"]:
            raise ValueError("Moteur SooperLooper non connecté")
        if index < 0 or index >= min(self.state["loop_count"], MAX_LOOPS):
            raise ValueError("Boucle invalide")
        if command not in COMMANDS or phase not in ("down", "up", "hit"):
            raise ValueError("Commande invalide")
        path = "/sl/{}/{}".format(index, phase)
        self.send(path, ("s", command))

    def set_loop(self, index, control, value):
        index = int(index)
        if not self.state["connected"]:
            raise ValueError("Moteur SooperLooper non connecté")
        if index < 0 or index >= min(self.state["loop_count"], MAX_LOOPS):
            raise ValueError("Boucle invalide")
        if control not in LOOP_SET_CONTROLS:
            raise ValueError("Paramètre de boucle invalide")
        if control in {"sync", "playback_sync", "round", "relative_sync"} and value not in (0, 1):
            raise ValueError("Ce paramètre attend une valeur 0 ou 1")
        if control == "quantize" and (not float(value).is_integer() or int(value) not in (0, 1, 2, 3)):
            raise ValueError("Valeur Quantize invalide")
        self.send("/sl/{}/set".format(index), ("s", control), ("f", value))

    def set_global(self, control, value):
        if not self.state["connected"]:
            raise ValueError("Moteur SooperLooper non connecté")
        if control not in GLOBAL_SET_CONTROLS:
            raise ValueError("Paramètre global invalide")
        if control == "sync_source" and (
            not float(value).is_integer() or int(value) not in range(-3, MAX_LOOPS + 1)
        ):
            raise ValueError("Source de synchronisation invalide")
        self.send("/set", ("s", control), ("f", value))

    def load_loop(self, index, filename):
        index = int(index)
        if not self.state["connected"]:
            raise ValueError("Moteur SooperLooper non connecté")
        if index < 0 or index >= min(self.state["loop_count"], MAX_LOOPS):
            raise ValueError("Boucle invalide")
        if not isinstance(filename, str) or not filename.strip() or len(filename) > 2048 or "\0" in filename:
            raise ValueError("Chemin de fichier invalide")
        self.send(
            "/sl/{}/load_loop".format(index),
            ("s", filename), ("s", self.callback_url), ("s", "/error"),
        )

    def save_loop(self, index, filename):
        index = int(index)
        if not self.state["connected"]:
            raise ValueError("Moteur SooperLooper non connecté")
        if index < 0 or index >= min(self.state["loop_count"], MAX_LOOPS):
            raise ValueError("Boucle invalide")
        if not isinstance(filename, str) or not filename.strip() or len(filename) > 2048 or "\0" in filename:
            raise ValueError("Chemin de fichier invalide")
        self.send(
            "/sl/{}/save_loop".format(index),
            ("s", filename), ("s", "float"), ("s", "little"),
            ("s", self.callback_url), ("s", "/error"),
        )

    def add_loop(self, channels):
        if not self.state["connected"]:
            raise ValueError("Moteur SooperLooper non connecté")
        if channels not in (1, 2):
            raise ValueError("Nombre de canaux invalide")
        with self.loop_lock:
            effective_count = self.state["loop_count"] + self.pending_loop_adds - self.pending_loop_removes
            if effective_count >= MAX_LOOPS:
                raise ValueError("slweb est limité à quatre boucles")
            self.pending_loop_adds += 1
        try:
            self.send("/loop_add", ("i", channels), ("f", 0))
        except OSError:
            with self.loop_lock:
                self.pending_loop_adds -= 1
            raise

    def remove_loop(self):
        if not self.state["connected"]:
            raise ValueError("Moteur SooperLooper non connecté")
        with self.loop_lock:
            effective_count = self.state["loop_count"] + self.pending_loop_adds - self.pending_loop_removes
            if effective_count <= 0:
                raise ValueError("Aucune boucle à supprimer")
            self.pending_loop_removes += 1
        try:
            self.send("/loop_del", ("i", -1))
        except OSError:
            with self.loop_lock:
                self.pending_loop_removes -= 1
            raise


def _asset_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = (
        os.path.join(here, "slweb_web"),
        os.path.join(os.path.dirname(here), "share", "sooperlooper", "slweb"),
    )
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    return candidates[0]


def make_handler(app, assets):
    class Handler(BaseHTTPRequestHandler):
        server_version = "slweb/0.1"

        def log_message(self, fmt, *args):
            sys.stderr.write("slweb: " + fmt % args + "\n")

        def _json(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/api/state":
                self._json(200, app.snapshot())
                return
            asset = "index.html" if self.path in ("/", "/index.html") else self.path.lstrip("/")
            if asset not in ("index.html", "app.css", "app.js"):
                self.send_error(404)
                return
            try:
                with open(os.path.join(assets, asset), "rb") as source:
                    body = source.read()
            except OSError:
                self.send_error(500, "Fichiers de l'interface introuvables")
                return
            content_type = "text/html; charset=utf-8" if asset.endswith(".html") else (
                "text/css; charset=utf-8" if asset.endswith(".css") else "text/javascript; charset=utf-8"
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if not self.path.startswith("/api/"):
                self.send_error(404)
                return
            length = self.headers.get("Content-Length", "")
            if not length.isdigit() or int(length) > MAX_BODY:
                self._json(413, {"error": "Requête trop grande"})
                return
            try:
                data = json.loads(self.rfile.read(int(length)).decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("Corps JSON invalide")
                if self.path == "/api/command":
                    app.command(data.get("index"), data.get("command"), data.get("phase"))
                elif self.path == "/api/set":
                    value = float(data.get("value"))
                    if not math.isfinite(value):
                        raise ValueError("Valeur numérique invalide")
                    if data.get("scope") == "global":
                        app.set_global(data.get("control"), value)
                    else:
                        app.set_loop(data.get("index"), data.get("control"), value)
                elif self.path == "/api/loops":
                    if data.get("action") == "add":
                        app.add_loop(int(data.get("channels", 2)))
                    elif data.get("action") == "remove":
                        app.remove_loop()
                    else:
                        raise ValueError("Action invalide")
                elif self.path == "/api/load":
                    app.load_loop(data.get("index"), data.get("filename"))
                elif self.path == "/api/save":
                    app.save_loop(data.get("index"), data.get("filename"))
                else:
                    self.send_error(404)
                    return
                self._json(200, {"ok": True})
            except (ValueError, TypeError, OSError) as error:
                self._json(400, {"error": str(error)})

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description="Interface web minimale pour SooperLooper (OSC)")
    parser.add_argument("--osc-host", default="127.0.0.1", help="hôte du moteur SooperLooper (défaut: localhost)")
    parser.add_argument("--osc-port", type=int, default=9951, help="port OSC du moteur (défaut: 9951)")
    parser.add_argument("--callback-port", type=int, default=0, help="port OSC de retour; 0 = automatique")
    parser.add_argument("--http-host", default="127.0.0.1", help="adresse HTTP (défaut: localhost)")
    parser.add_argument("--http-port", type=int, default=8080, help="port HTTP (défaut: 8080)")
    args = parser.parse_args(argv)

    if not (1 <= args.osc_port <= 65535 and 0 <= args.callback_port <= 65535):
        parser.error("port OSC invalide")
    if not (1 <= args.http_port <= 65535):
        parser.error("port HTTP invalide")
    app = SlWeb(args.osc_host, args.osc_port, "0.0.0.0", args.callback_port)
    app.start()
    server = ThreadingHTTPServer((args.http_host, args.http_port), make_handler(app, _asset_dir()))
    server.daemon_threads = True
    print("slweb: http://{}:{}/ (moteur OSC {}:{})".format(
        args.http_host, args.http_port, args.osc_host, args.osc_port
    ), flush=True)
    if args.http_host not in ("127.0.0.1", "localhost", "::1"):
        print("slweb: attention, HTTP est accessible sur le réseau local sans authentification", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nslweb: arrêt")
    finally:
        server.server_close()
        app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
