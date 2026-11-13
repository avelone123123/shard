import unittest
import os
import sys
import tempfile
import time
import json
from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient
from shard_engine import paths, storage, settings, server, training
from shard_engine import net_identity, net_discovery, net_tunnel, net_wireguard, net_strategy
from shard_engine import net_platform_win, net_platform_linux, cli
class TestNetIdentity(unittest.TestCase):
    def test_identity_lifecycle_and_crypto(self):
        with tempfile.TemporaryDirectory() as td:
            id_path = os.path.join(td, "node_identity.json")
            node = net_identity.NodeIdentity(id_path)
            self.assertTrue(node.node_id.startswith("shn_"))
            self.assertEqual(len(node.node_id), 44)
            node2 = net_identity.NodeIdentity(id_path)
            self.assertEqual(node.node_id, node2.node_id)
            self.assertEqual(node.pubkey_bytes(), node2.pubkey_bytes())
            payload = b"test_payload_bytes"
            sig = node.sign_bytes(net_identity.DOMAIN_DISC_REG, payload)
            self.assertTrue(net_identity.verify_sig(node.pubkey_b64, node.node_id, net_identity.DOMAIN_DISC_REG, payload, sig))
            self.assertFalse(net_identity.verify_sig(node.pubkey_b64, node.node_id, net_identity.DOMAIN_DISC_HB, payload, sig))
            tampered = b"test_payload_tampered"
            self.assertFalse(net_identity.verify_sig(node.pubkey_b64, node.node_id, net_identity.DOMAIN_DISC_REG, tampered, sig))
            priv_x, pub_x = net_identity.x25519_keypair()
            priv_y, pub_y = net_identity.x25519_keypair()
            sec_x = net_identity.x25519_shared_secret(priv_x, pub_y)
            sec_y = net_identity.x25519_shared_secret(priv_y, pub_x)
            self.assertEqual(sec_x, sec_y)
            k_c2h, k_h2c, iv_c, iv_h = net_identity.derive_session_keys(sec_x, b"test_salt_h_final_32_bytes_pad_")
            aad = net_identity.pack_frame_aad(1, net_identity.MSG_HTTP_REQ, 0, 1, 1)
            ct = net_identity.aead_encrypt(k_c2h, iv_c, 1, aad, b"hello_shard")
            pt = net_identity.aead_decrypt(k_c2h, iv_c, 1, aad, ct)
            self.assertEqual(pt, b"hello_shard")
            with self.assertRaises(Exception):
                net_identity.aead_decrypt(k_c2h, iv_c, 2, aad, ct)
class TestNetDiscovery(unittest.TestCase):
    def test_discovery_store_and_client(self):
        store = net_discovery.DiscoveryStore(default_ttl=5, min_ttl=2, max_ttl=10)
        node = net_identity.NodeIdentity()
        meta = {"name": "TestNode", "active_model": "test/model", "visibility": "public", "access_policy": "open"}
        env = node.sign_envelope("register", meta)
        code1, reg_res = store.register(env)
        self.assertEqual(code1, 200)
        lookup = store.lookup(node.node_id)
        self.assertIsNotNone(lookup)
        self.assertEqual(lookup.get("node_id"), node.node_id)
        self.assertEqual(lookup.get("listing", {}).get("name"), "TestNode")
        code2, res2 = store.register(env)
        self.assertEqual(code2, 409)
        listed = store.list_servers(q="TestNode")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["node_id"], node.node_id)
        commit = net_discovery.commitment_for_invite("inv_123")
        c_env = node.sign_envelope("relay_register", {"commitment": commit, "hint": {"rendezvous": "direct"}, "ttl_s": 5})
        code_p, res_p = store.publish_private_commitment(c_env)
        self.assertEqual(code_p, 200)
        found_c = store.lookup_private_commitment(commit)
        self.assertIsNotNone(found_c)
        unreg_env = node.sign_envelope("unregister", {})
        store.unregister(unreg_env)
        self.assertIsNone(store.lookup(node.node_id))
class TestNetTunnelSecurity(unittest.TestCase):
    def test_blind_relay_and_isolation(self):
        relay_hub = net_tunnel.RelayHub(auth_token="relay_sec", max_tunnels_per_host=4)
        self.assertEqual(relay_hub.stats().get("active_tunnels"), 0)
        sec = net_tunnel.get_bridge_secret()
        self.assertTrue(net_tunnel.verify_bridge_token(sec))
        self.assertFalse(net_tunnel.verify_bridge_token("wrong_token"))
        req_mock = MagicMock()
        req_mock.scope = {"shard_tunnel": {"peer_node_id": "shn_0123456789abcdef0123456789abcdef01234567"}}
        req_mock.headers = {}
        self.assertTrue(net_tunnel.is_tunnel_request(req_mock))
        self.assertEqual(net_tunnel.tunnel_peer_id(req_mock), "shn_0123456789abcdef0123456789abcdef01234567")
        req_bridge = MagicMock()
        req_bridge.scope = {}
        req_bridge.headers = {"x-shard-bridge-token": sec, "x-shard-tunnel-peer": "shn_0123456789abcdef0123456789abcdef01234567"}
        self.assertTrue(net_tunnel.is_tunnel_request(req_bridge))
        self.assertEqual(net_tunnel.tunnel_peer_id(req_bridge), "shn_0123456789abcdef0123456789abcdef01234567")
        req_fake = MagicMock()
        req_fake.scope = {}
        req_fake.headers = {"x-shard-bridge-token": "bad", "x-shard-tunnel-peer": "shn_fake"}
        self.assertFalse(net_tunnel.is_tunnel_request(req_fake))
class TestNetStrategyAndManager(unittest.TestCase):
    def test_network_manager_lifecycle(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["SHARD_DATA_DIR"] = td
            net_strategy._DEFAULT_MANAGER = None
            mgr = net_strategy.get_network_manager()
            st = mgr.status()
            self.assertIn("node_id", st)
            self.assertEqual(st.get("state"), "ready")
            inv = mgr.create_invite(ttl_s=3600, max_uses=2, label="unit_test")
            self.assertIn("invite_id", inv)
            self.assertIn("uri", inv)
            invs = mgr.list_invites()
            self.assertTrue(any(x["invite_id"] == inv["invite_id"] for x in invs))
            rev = mgr.revoke_invite(inv["invite_id"])
            self.assertTrue(rev)
            peer_id = "shn_1111222233334444555566667777888899990000"
            self.assertTrue(mgr.approve_peer(peer_id))
            auth_peers = [p["node_id"] if isinstance(p, dict) else p for p in mgr.list_authorized_peers()]
            self.assertIn(peer_id, auth_peers)
            quota_res = mgr.set_peer_quota(peer_id, max_requests_per_min=60, bandwidth_kbps=2048)
            self.assertEqual(quota_res.get("peer", {}).get("max_requests_per_min"), 60)
            self.assertTrue(mgr.kick_peer(peer_id))
            self.assertTrue(mgr.block_peer(peer_id))
            rev_peers = [p["node_id"] if isinstance(p, dict) else p for p in mgr.list_revoked_peers()]
            self.assertIn(peer_id, rev_peers)
            self.assertTrue(mgr.unblock_peer(peer_id))
            diag = mgr.diagnostics(probe_public=False)
            self.assertIn("platform", diag)
            self.assertIn("wireguard", diag)
            net_strategy._DEFAULT_MANAGER = None
class TestServerNetEndpoints(unittest.TestCase):
    def test_api_net_routes_and_isolation(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["SHARD_DATA_DIR"] = td
            net_strategy._DEFAULT_MANAGER = None
            client = TestClient(server.app)
            owner_tok = training.get_owner_token()
            h_owner = {"X-Shard-Owner-Token": owner_tok}
            r_stat = client.get("/api/net/status", headers=h_owner)
            self.assertEqual(r_stat.status_code, 200)
            self.assertIn("node_id", r_stat.json())
            r_ident = client.get("/api/net/identity", headers=h_owner)
            self.assertEqual(r_ident.status_code, 200)
            self.assertEqual(r_ident.json().get("node_id"), r_stat.json()["node_id"])
            r_cfg = client.get("/api/net/config", headers=h_owner)
            self.assertEqual(r_cfg.status_code, 200)
            r_up = client.post("/api/net/config", json={"visibility": "public", "max_peers": 12}, headers=h_owner)
            self.assertEqual(r_up.status_code, 200)
            self.assertEqual(r_up.json().get("config", {}).get("visibility"), "public")
            r_inv = client.post("/api/net/invites", json={"ttl_s": 1800, "label": "api_test"}, headers=h_owner)
            self.assertEqual(r_inv.status_code, 200)
            iid = r_inv.json().get("invite", {}).get("id")
            self.assertTrue(iid)
            r_invs = client.get("/api/net/invites", headers=h_owner)
            self.assertEqual(r_invs.status_code, 200)
            self.assertTrue(any(x.get("id") == iid for x in r_invs.json().get("invites", [])))
            r_del = client.delete(f"/api/net/invites/{iid}", headers=h_owner)
            self.assertEqual(r_del.status_code, 200)
            self.assertTrue(r_del.json().get("revoked"))
            sec = net_tunnel.get_bridge_secret()
            h_tun = {"x-shard-bridge-token": sec, "x-shard-tunnel-peer": "shn_fake"}
            r_sec_breach = client.get("/api/net/status", headers=h_tun)
            self.assertEqual(r_sec_breach.status_code, 403)
            r_spoof = client.get("/api/net/status", headers={"X-Shard-Owner-Token": owner_tok, "x-shard-tunnel-peer": "shn_fake"})
            self.assertEqual(r_spoof.status_code, 403)
            r_ping = client.get("/v1/ping")
            self.assertEqual(r_ping.status_code, 200)
            r_models = client.get("/v1/models", headers=h_tun)
            self.assertEqual(r_models.status_code, 200)
            net_strategy._DEFAULT_MANAGER = None
class TestCliNetwork(unittest.TestCase):
    def test_cli_network_commands(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["SHARD_DATA_DIR"] = td
            parser = cli._build_parser()
            args = parser.parse_args(["network", "status", "--json"])
            self.assertEqual(args.command, "network")
            self.assertEqual(args.net_action, "status")
            self.assertTrue(args.as_json)
            args_id = parser.parse_args(["network", "identity", "--json"])
            self.assertEqual(args_id.net_action, "identity")
            args_cfg = parser.parse_args(["network", "config", "--offline-only", "true", "--mode", "relay"])
            self.assertEqual(args_cfg.offline_only, "true")
            self.assertEqual(args_cfg.mode, "relay")
            args_inv = parser.parse_args(["network", "invite", "create", "--ttl", "3600"])
            self.assertEqual(args_inv.inv_action, "create")
            self.assertEqual(args_inv.ttl, 3600)
            args_doc = parser.parse_args(["network", "doctor", "--json"])
            self.assertEqual(args_doc.net_action, "doctor")
            self.assertTrue(args_doc.as_json)
if __name__ == "__main__":
    unittest.main()
