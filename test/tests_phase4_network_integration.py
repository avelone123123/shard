import asyncio
import os
import struct
import tempfile
import unittest
from shard_engine import net_discovery, net_identity, net_strategy, net_tunnel
class TestPhase4NetworkIntegration(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self._prev_data_dir = os.environ.get("SHARD_DATA_DIR")
        os.environ["SHARD_DATA_DIR"] = self._temp_dir.name
        os.environ["SHARD_ALLOW_INSECURE_LOCAL"] = "1"
    def tearDown(self):
        if self._prev_data_dir is not None:
            os.environ["SHARD_DATA_DIR"] = self._prev_data_dir
        else:
            os.environ.pop("SHARD_DATA_DIR", None)
        self._temp_dir.cleanup()
    def test_publish_server_parameter_aliases(self):
        mgr = net_strategy.NetworkManager()
        res = mgr.publish_server(
            name="AliasNode",
            visibility="public",
            access_policy="invite_only",
            max_peers=8,
            direct_port=62000,
            models=["Qwen2.5"],
            offline_only=True,
        )
        self.assertTrue(res.get("published"))
        self.assertEqual(res.get("display_name"), "AliasNode")
        self.assertEqual(res.get("max_participants"), 8)
        self.assertEqual(res.get("direct_p2p_port"), 62000)
        mgr.shutdown()
    def test_publish_server_strict_rejection_of_unknown_kwargs(self):
        mgr = net_strategy.NetworkManager()
        with self.assertRaises(TypeError):
            mgr.publish_server(invalid_test_arg=999)
        mgr.shutdown()
    def test_connect_parameter_aliases(self):
        mgr = net_strategy.NetworkManager()
        target = "shard://connect?node_id=shn_" + "1" * 40 + "&name=RemoteNode"
        res = mgr.connect(target, mode="relay")
        self.assertIn("attempts", res)
        mgr.shutdown()
    def test_connect_strict_rejection_of_unknown_kwargs(self):
        mgr = net_strategy.NetworkManager()
        target = "shard://connect?node_id=shn_" + "1" * 40 + "&name=RemoteNode"
        with self.assertRaises(TypeError):
            mgr.connect(target, invalid_test_arg=999)
        mgr.shutdown()
    def test_cli_network_create_open_flag(self):
        from shard_engine import cli
        parser = cli._build_parser()
        args = parser.parse_args(["network", "create", "--name", "MyHost", "--open"])
        self.assertEqual(args.name, "MyHost")
        self.assertTrue(args.open)
    def test_blind_relay_and_discovery_e2e(self):
        store = net_discovery.DiscoveryStore()
        ident_host = net_identity.NodeIdentity(os.path.join(self._temp_dir.name, "host_id.json"))
        reg_env = ident_host.sign_envelope("register", {
            "listing": {
                "name": "IntegrationHost",
                "role": "server",
                "status": "online",
                "visibility": "public",
                "access_mode": "open",
                "models": ["Qwen2.5"],
            },
            "ttl_s": 60,
        })
        status_code, reg_res = store.register(reg_env)
        self.assertEqual(status_code, 200)
        servers = store.list_servers()
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["listing"]["name"], "IntegrationHost")
        unreg_env = ident_host.sign_envelope("unregister", {"node_id": ident_host.node_id})
        del_code, _ = store.unregister(unreg_env)
        self.assertEqual(del_code, 200)
        self.assertEqual(len(store.list_servers()), 0)
    def test_owner_header_spoofing_defense(self):
        from starlette.requests import Request
        from shard_engine import server
        scope_remote = {
            "type": "http",
            "method": "GET",
            "path": "/api/net/peers",
            "headers": [(b"host", b"192.168.100.6"), (b"x-forwarded-for", b"127.0.0.1")],
            "client": ("192.168.100.6", 54321),
        }
        req_remote = Request(scope_remote)
        self.assertFalse(server._is_owner_request(req_remote))
        scope_tunnel = {
            "type": "http",
            "method": "GET",
            "path": "/api/net/peers",
            "headers": [(b"host", b"127.0.0.1"), (b"x-shard-tunnel-peer", b"shn_" + b"a" * 40), (b"x-shard-bridge-token", b"badtoken")],
            "client": ("127.0.0.1", 54321),
            "shard_tunnel": {"peer_node_id": "shn_" + "a" * 40},
        }
        req_tunnel = Request(scope_tunnel)
        self.assertFalse(server._is_owner_request(req_tunnel))
    def test_stun_packet_encoding_decoding(self):
        tx_id = b"\x01" * 12
        req = struct.pack("!HHI12s", 0x0001, 0, net_strategy._STUN_MAGIC, tx_id)
        self.assertEqual(len(req), 20)
        xport = 54321 ^ (net_strategy._STUN_MAGIC >> 16)
        xip = 0x7F000001 ^ net_strategy._STUN_MAGIC
        attr = struct.pack("!HHBBHI", 0x0020, 8, 0, 1, xport, xip)
        resp = struct.pack("!HHI12s", 0x0101, len(attr), net_strategy._STUN_MAGIC, tx_id) + attr
        parsed = net_strategy._parse_stun_response(resp, tx_id)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[0], "127.0.0.1")
        self.assertEqual(parsed[1], 54321)
    def test_relay_hub_max_bandwidth_alias(self):
        hub = net_tunnel.RelayHub(max_bandwidth_kbps=2000)
        self.assertEqual(hub.max_bytes_per_sec, 250000.0)
if __name__ == "__main__":
    unittest.main()