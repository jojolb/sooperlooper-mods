import importlib.util
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlparse


MODULE_PATH = os.path.join(os.path.dirname(__file__), "slweb.py")
SPEC = importlib.util.spec_from_file_location("slweb", MODULE_PATH)
slweb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(slweb)


class OscCodecTests(unittest.TestCase):
    def test_round_trip_strings_int_and_float(self):
        packet = slweb.encode_osc("/sl/2/set", (("s", "feedback"), ("f", 0.75), ("i", 4)))
        path, values = slweb.decode_osc(packet)
        self.assertEqual(path, "/sl/2/set")
        self.assertEqual(values[0], "feedback")
        self.assertAlmostEqual(values[1], 0.75)
        self.assertEqual(values[2], 4)

    def test_decode_rejects_unsupported_types(self):
        packet = slweb._osc_string("/test") + slweb._osc_string(",T")
        with self.assertRaises(ValueError):
            slweb.decode_osc(packet)


class LoopLimitTests(unittest.TestCase):
    def setUp(self):
        self.files = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.files.name, "loops"))
        with open(os.path.join(self.files.name, "loops", "take.wav"), "wb") as wav:
            wav.write(b"test")
        self.app = slweb.SlWeb("127.0.0.1", 9951, "127.0.0.1", 0, self.files.name)
        self.sent = []
        self.app.send = lambda path, *args: self.sent.append((path, args))

    def tearDown(self):
        self.app.close()
        self.files.cleanup()

    def test_only_four_loops_are_exposed(self):
        self.app._handle_pingack(("osc.udp://127.0.0.1:9951/", "test", 6))
        self.assertEqual(len(self.app.snapshot()["loops"]), 4)
        self.assertEqual(self.app.snapshot()["hidden_loops"], 2)

    def test_add_loop_is_limited_to_four(self):
        self.app.state["connected"] = True
        self.app.state["loop_count"] = 3
        self.app.add_loop(2)
        with self.assertRaises(ValueError):
            self.app.add_loop(2)
        self.assertEqual(self.app.pending_loop_adds, 1)

    def test_commands_and_controls_are_validated(self):
        self.app.state["loop_count"] = 1
        with self.assertRaises(ValueError):
            self.app.command(0, "quit", "down")
        with self.assertRaises(ValueError):
            self.app.set_loop(0, "state", 1)
        with self.assertRaises(ValueError):
            self.app.set_global("save_loop", 1)

    def test_sync_source_and_load_loop_use_engine_osc(self):
        self.app.state["connected"] = True
        self.app.state["loop_count"] = 1
        self.app.set_global("sync_source", -3)
        self.app.set_loop(0, "sync", 1)
        self.app.set_loop(0, "playback_sync", 1)
        self.app.set_loop(0, "wet", 0.43)
        self.app.set_global("quantize", 2)
        self.app.load_loop(0, "loops/take.wav")
        self.app.save_loop(0, "loops/out.wav")
        self.app._handle_osc("/ctrl", [0, "wet", 0.62])
        self.app._handle_osc("/ctrl", [0, "quantize", 2])
        self.assertEqual(self.sent[0][0], "/set")
        self.assertEqual(self.sent[1][0], "/sl/0/set")
        self.assertEqual(self.sent[1][1], (("s", "sync"), ("f", 1)))
        self.assertEqual(self.sent[2][1], (("s", "playback_sync"), ("f", 1)))
        self.assertEqual(self.sent[3][1], (("s", "wet"), ("f", 0.43)))
        self.assertEqual(self.sent[4], (
            "/sl/-1/set", (("s", "quantize"), ("f", 2))
        ))
        self.assertEqual(self.sent[5][0], "/sl/0/load_loop")
        self.assertEqual(self.sent[5][1][0], (
            "s", os.path.realpath(os.path.join(self.files.name, "loops", "take.wav"))
        ))
        self.assertEqual(self.sent[6][0], "/sl/0/save_loop")
        self.assertEqual(self.sent[6][1][:3], (
            ("s", os.path.realpath(os.path.join(self.files.name, "loops", "out.wav"))),
            ("s", "float"), ("s", "little")
        ))
        self.assertEqual(self.app.snapshot()["loops"][0]["wet"], 0.62)
        self.assertEqual(self.app.snapshot()["globals"]["quantize"], 2)
        with self.assertRaises(ValueError):
            self.app.set_global("sync_source", 5)
        with self.assertRaises(ValueError):
            self.app.set_global("quantize", 4)
        with self.assertRaises(ValueError):
            self.app.set_loop(0, "quantize", 2)

    def test_sync_source_keeps_the_requested_value_until_engine_confirms_it(self):
        self.app.state["connected"] = True
        self.app.state["loop_count"] = 2

        self.app.set_global("sync_source", 2)
        self.app._handle_osc("/ctrl", [-2, "sync_source", 1])
        self.assertEqual(self.app.snapshot()["pending_globals"]["sync_source"], 2)
        self.assertNotIn("sync_source", self.app.snapshot()["globals"])

        self.app._handle_osc("/ctrl", [-2, "sync_source", 2])
        self.assertEqual(self.app.snapshot()["globals"]["sync_source"], 2)
        self.assertEqual(self.app.snapshot()["pending_globals"]["sync_source"], 2)

        # A delayed initial /get response must not overwrite the confirmed
        # selection after the matching update has arrived.
        self.app._handle_osc("/ctrl", [-2, "sync_source", 1])
        self.assertEqual(self.app.snapshot()["globals"]["sync_source"], 2)
        self.assertEqual(self.sent[0], ("/set", (("s", "sync_source"), ("f", 2))))

    def test_waiting_states_are_named_for_start_and_stop(self):
        self.app.state["loop_count"] = 1
        self.app._handle_osc("/ctrl", [0, "state", 1])
        self.app._handle_osc("/ctrl", [0, "waiting", 1])
        self.assertEqual(self.app.snapshot()["loops"][0]["state_label"], "waiting start…")
        self.assertEqual(self.app.snapshot()["loops"][0]["waiting"], 1)

        self.app._handle_osc("/ctrl", [0, "state", 3])
        self.assertEqual(self.app.snapshot()["loops"][0]["state_label"], "waiting stop…")

    def test_new_loops_inherit_the_global_quantize_value(self):
        self.app.state["loop_count"] = 1
        self.app.state["globals"]["quantize"] = 3
        self.app._handle_pingack(("osc.udp://127.0.0.1:9951/", "test", 2, 7))
        self.assertIn((
            "/sl/1/set", (("s", "quantize"), ("f", 3))
        ), self.sent)

    def test_file_paths_cannot_escape_root_and_only_wav_is_listed(self):
        listing = self.app.list_files("")
        self.assertEqual([entry["name"] for entry in listing["entries"]], ["loops"])
        with self.assertRaises(ValueError):
            self.app.resolve_directory("../")
        with self.assertRaises(ValueError):
            self.app.resolve_file("../outside.wav", for_save=True)
        with self.assertRaises(ValueError):
            self.app.resolve_file("loops/take.txt", for_save=True)
        outside = tempfile.TemporaryDirectory()
        try:
            os.symlink(outside.name, os.path.join(self.files.name, "escape"), target_is_directory=True)
        except (OSError, NotImplementedError):
            outside.cleanup()
            self.skipTest("Les liens symboliques ne sont pas disponibles")
        try:
            with self.assertRaises(ValueError):
                self.app.resolve_directory("escape")
            with self.assertRaises(ValueError):
                self.app.resolve_file("escape/out.wav", for_save=True)
        finally:
            outside.cleanup()


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.files = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.files.name, "sets"))
        with open(os.path.join(self.files.name, "sets", "take.wav"), "wb") as wav:
            wav.write(b"test")
        with open(os.path.join(self.files.name, "ignore.txt"), "w", encoding="utf-8") as text:
            text.write("not audio")
        self.app = slweb.SlWeb("127.0.0.1", 9951, "127.0.0.1", 0, self.files.name)
        self.app.state["connected"] = True
        self.app.state["loop_count"] = 1
        self.sent = []
        self.app.send = lambda path, *args: self.sent.append((path, args))
        handler = slweb.make_handler(self.app, slweb._asset_dir())
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = "http://127.0.0.1:{}".format(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        self.files.cleanup()

    def test_serves_interface_and_state(self):
        with urlopen(self.base_url + "/") as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"SooperLooper", response.read())
        with urlopen(self.base_url + "/api/state") as response:
            self.assertEqual(json.load(response)["loop_count"], 1)

    def test_command_api_sends_osc_command(self):
        body = json.dumps({"index": 0, "command": "record", "phase": "down"}).encode()
        request = Request(self.base_url + "/api/command", data=body, headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            self.assertEqual(json.load(response), {"ok": True})
        self.assertEqual(self.sent[0][0], "/sl/0/down")

    def test_global_quantize_api_broadcasts_to_all_loops(self):
        body = json.dumps({
            "scope": "global", "control": "quantize", "value": 3
        }).encode()
        request = Request(self.base_url + "/api/set", data=body, headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            self.assertEqual(json.load(response), {"ok": True})
        self.assertEqual(self.sent[0], (
            "/sl/-1/set", (("s", "quantize"), ("f", 3.0))
        ))

    def test_load_api_sends_engine_path(self):
        body = json.dumps({"index": 0, "filename": "sets/take.wav"}).encode()
        request = Request(self.base_url + "/api/load", data=body, headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            self.assertEqual(json.load(response), {"ok": True})
        self.assertEqual(self.sent[0][0], "/sl/0/load_loop")
        self.assertEqual(
            self.sent[0][1][0][1],
            os.path.realpath(os.path.join(self.files.name, "sets", "take.wav")),
        )

    def test_save_api_sends_engine_path(self):
        body = json.dumps({"index": 0, "filename": "sets/mix.wav"}).encode()
        request = Request(self.base_url + "/api/save", data=body, headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            self.assertEqual(json.load(response), {"ok": True})
        self.assertEqual(self.sent[0][0], "/sl/0/save_loop")

    def test_save_api_requires_overwrite_confirmation(self):
        body = json.dumps({"index": 0, "filename": "sets/take.wav"}).encode()
        request = Request(self.base_url + "/api/save", data=body, headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.sent, [])

    def test_file_api_browses_wav_and_rejects_traversal(self):
        with urlopen(self.base_url + "/api/files") as response:
            data = json.load(response)
        self.assertEqual([entry["name"] for entry in data["entries"]], ["sets"])
        with urlopen(self.base_url + "/api/files?path=sets") as response:
            data = json.load(response)
        self.assertEqual([entry["name"] for entry in data["entries"]], ["take.wav"])
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base_url + "/api/files?path=..")
        self.assertEqual(error.exception.code, 400)

    def test_save_requires_confirmation_before_overwriting(self):
        body = json.dumps({"index": 0, "filename": "sets/take.wav"}).encode()
        request = Request(self.base_url + "/api/save", data=body, headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.sent, [])


class FakeOscEngine:
    def __init__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(0.2)
        self.port = self.socket.getsockname()[1]
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.socket.close()
        self.thread.join(timeout=1)

    def run(self):
        while not self.stopped.is_set():
            try:
                packet, _ = self.socket.recvfrom(65535)
                path, args = slweb.decode_osc(packet)
                if path == "/ping":
                    callback = urlparse(args[0])
                    reply = slweb.encode_osc(args[1], (
                        ("s", "osc.udp://127.0.0.1:{}/".format(self.port)),
                        ("s", "test-engine"), ("i", 1), ("i", 7),
                    ))
                    self.socket.sendto(reply, (callback.hostname, callback.port))
                elif path == "/sl/0/get":
                    callback = urlparse(args[1])
                    value = {
                        "state": 4, "loop_pos": 2.5, "loop_len": 8.0,
                        "feedback": 0.8, "input_gain": 0.5,
                    }.get(args[0], 1.0)
                    reply = slweb.encode_osc(args[2], (("i", 0), ("s", args[0]), ("f", value)))
                    self.socket.sendto(reply, (callback.hostname, callback.port))
                elif path == "/sl/0/get_prop":
                    callback = urlparse(args[1])
                    reply = slweb.encode_osc(args[2], (("i", 0), ("s", args[0]), ("s", "Loop 1")))
                    self.socket.sendto(reply, (callback.hostname, callback.port))
            except socket.timeout:
                continue
            except OSError:
                break
            except (ValueError, IndexError):
                continue


class OscBridgeTests(unittest.TestCase):
    def test_discovers_engine_and_receives_loop_state(self):
        engine = FakeOscEngine()
        engine.start()
        app = slweb.SlWeb("127.0.0.1", engine.port, "127.0.0.1", 0)
        try:
            app.start()
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                snapshot = app.snapshot()
                if snapshot["connected"] and snapshot["loops"] and "state" in snapshot["loops"][0]:
                    break
                time.sleep(0.025)
            self.assertTrue(snapshot["connected"])
            self.assertEqual(snapshot["engine_version"], "test-engine")
            self.assertEqual(snapshot["loops"][0]["state"], 4)
            self.assertEqual(snapshot["loops"][0]["loop_pos"], 2.5)
        finally:
            app.close()
            engine.close()


if __name__ == "__main__":
    unittest.main()
