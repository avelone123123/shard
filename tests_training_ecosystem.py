import importlib
import json
import os
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock
from fastapi.testclient import TestClient
def _make_mock_soup_script(base_dir, mode="success"):
    script = Path(base_dir) / f"mock_soup_{mode}.py"
    code = textwrap.dedent("""\
    import json
    import os
    import sys
    import time
    from pathlib import Path
    def _arg(flag, default=None):
        if flag in sys.argv:
            idx = sys.argv.index(flag)
            if idx + 1 < len(sys.argv):
                return sys.argv[idx + 1]
        return default
    def _read_yaml_simple(path):
        txt = Path(path).read_text(encoding="utf-8")
        out_dir = None
        for ln in txt.splitlines():
            s = ln.strip()
            if s.startswith("output:"):
                raw = s.split(":", 1)[1].strip()
                if raw.startswith('"') and raw.endswith('"'):
                    out_dir = json.loads(raw)
                else:
                    out_dir = raw
        return out_dir
    if "--version" in sys.argv:
        print("soup 0.77.2")
        sys.exit(0)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    sub = sys.argv[2] if len(sys.argv) > 2 else ""
    if cmd == "data" and sub == "validate":
        p = Path(sys.argv[3])
        if not p.is_file():
            print("dataset missing", file=sys.stderr)
            sys.exit(2)
        lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        if not lines:
            sys.exit(2)
        print(f"Validated {len(lines)} records")
        sys.exit(0)
    if cmd == "train":
        cfg_path = _arg("--config")
        if not cfg_path or not Path(cfg_path).is_file():
            print("config missing", file=sys.stderr)
            sys.exit(1)
        if "--yes" not in sys.argv:
            print("missing --yes flag", file=sys.stderr)
            sys.exit(1)
        if "--dry-run" in sys.argv:
            print("DRY RUN OK:", cfg_path)
            sys.exit(0)
        mode = os.environ.get("MOCK_SOUP_MODE", "success")
        if mode == "slow":
            print("1/100 {'loss': 0.99, 'epoch': 0.1}", flush=True)
            time.sleep(15)
            sys.exit(0)
        if mode == "fail":
            print("simulated OOM crash", file=sys.stderr)
            sys.exit(2)
        out_dir = Path(_read_yaml_simple(cfg_path) or "./output")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "adapter_config.json").write_text(json.dumps({"base_model_name_or_path": "TinyLlama/TinyLlama-1.1B-Chat-v1.0", "r": 16}), encoding="utf-8")
        (out_dir / "adapter_model.safetensors").write_bytes(b"SAFETENSORS_MOCK")
        (out_dir / "checkpoint-10").mkdir(exist_ok=True)
        print("10/10 100%|###| {'loss': 0.42, 'epoch': 3.0}", flush=True)
        sys.exit(0)
    if cmd == "merge":
        out_dir = Path(_arg("--output"))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "config.json").write_text(json.dumps({"architectures": ["LlamaForCausalLM"]}), encoding="utf-8")
        (out_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
        (out_dir / "model.safetensors").write_bytes(b"MERGED_MOCK")
        print("Merged model saved to", out_dir)
        sys.exit(0)
    if cmd == "export":
        out_file = Path(_arg("-o"))
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"GGUF_MOCK_BYTES")
        print("Exported GGUF to", out_file)
        sys.exit(0)
    if cmd == "eval" and sub == "custom":
        model_ref = _arg("--model", "")
        tasks_p = Path(_arg("--tasks"))
        out_p = Path(_arg("--output"))
        n_tasks = len([x for x in tasks_p.read_text(encoding="utf-8").splitlines() if x.strip()])
        acc = 0.85 if ("output" in model_ref or "merged" in model_ref or "mock" in model_ref.lower() or os.path.exists(model_ref)) else 0.50
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps({
            "model": model_ref,
            "tasks": str(tasks_p),
            "total": n_tasks,
            "correct": int(round(n_tasks * acc)),
            "accuracy": acc,
            "category_scores": {"dataset_regression": {"total": n_tasks, "correct": int(round(n_tasks * acc)), "accuracy": acc}},
        }), encoding="utf-8")
        sys.exit(0)
    print("Unknown command", sys.argv, file=sys.stderr)
    sys.exit(1)
    """)
    script.write_text(code, encoding="utf-8")
    return f'"{sys.executable}" "{script}"'
class IsolatedShardEnvMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._old_env = dict(os.environ)
        os.environ["SHARD_DATA_DIR"] = self.tmp.name
        os.environ["SHARD_MODELS_DIR"] = str(Path(self.tmp.name) / "models")
        self.mock_soup_cmd = _make_mock_soup_script(self.tmp.name)
        os.environ["SHARD_SOUP_CMD"] = self.mock_soup_cmd
        os.environ.pop("SHARD_SOUP_PYTHON", None)
        import shard_engine.paths as p_mod
        import shard_engine.settings as s_mod
        import shard_engine.soup_adapter as sa_mod
        import shard_engine.feedback as fb_mod
        import shard_engine.training as tr_mod
        import shard_engine.lan as lan_mod
        import shard_engine.server as srv_mod
        import shard_engine.cli as cli_mod
        lan_mod.LAN.off(quiet=True)
        for m in (p_mod, s_mod, sa_mod, fb_mod, tr_mod, lan_mod, srv_mod, cli_mod):
            importlib.reload(m)
        self.paths = p_mod
        self.soup_adapter = sa_mod
        self.feedback = fb_mod
        self.training = tr_mod
        self.server = srv_mod
        self.cli = cli_mod
    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)
class TestSoupAdapterAndConfig(IsolatedShardEnvMixin, unittest.TestCase):
    def test_python_compat_and_config_builder(self):
        self.assertTrue(self.soup_adapter._py_compatible((3, 12, 8)))
        self.assertFalse(self.soup_adapter._py_compatible((3, 14, 0)))
        self.assertFalse(self.soup_adapter._py_compatible((3, 10, 12)))
        cfg, errs = self.soup_adapter.build_soup_config(
            base_model="Qwen/Qwen2.5-Coder-1.5B-Instruct",
            dataset_path="/tmp/ds.jsonl",
            output_dir="/tmp/out",
            task="sft",
            experiment_name="bad/name:here",
            params={"epochs": 2, "lr": 1e-4, "lora_r": 16, "quantization": "4bit"},
        )
        self.assertEqual(errs, [])
        self.assertEqual(cfg["task"], "sft")
        self.assertEqual(cfg["data"]["format"], "chatml")
        self.assertTrue(cfg["data"]["train_on_responses_only"])
        self.assertNotIn("/", cfg["experiment_name"])
        self.assertNotIn(":", cfg["experiment_name"])
        dpo_cfg, dpo_errs = self.soup_adapter.build_soup_config(
            base_model="Qwen/Qwen2.5-Coder-1.5B-Instruct",
            dataset_path="/tmp/dpo.jsonl",
            output_dir="/tmp/out",
            task="dpo",
        )
        self.assertEqual(dpo_errs, [])
        self.assertEqual(dpo_cfg["data"]["format"], "dpo")
        self.assertNotIn("train_on_responses_only", dpo_cfg["data"])
        _, bad_errs = self.soup_adapter.build_soup_config("", "", "", task="bogus", params={"epochs": 0, "lr": -1})
        self.assertGreaterEqual(len(bad_errs), 4)
        yaml_path = self.soup_adapter.write_soup_config(cfg, Path(self.tmp.name) / "test.yaml")
        yaml_txt = Path(yaml_path).read_text(encoding="utf-8")
        self.assertIn("base:", yaml_txt)
        self.assertIn("train_on_responses_only: true", yaml_txt)
    def test_detect_mock_soup(self):
        info = self.soup_adapter.detect_soup(probe_deps=False)
        self.assertTrue(info["available"])
        self.assertEqual(info["soup_version"], "0.77.2")
        self.assertTrue(info["capabilities"]["train"])
class TestPrivacyAndFeedbackPipeline(IsolatedShardEnvMixin, unittest.TestCase):
    def test_consent_and_secret_pii_redaction(self):
        with self.assertRaises(PermissionError):
            self.feedback.submit_feedback({
                "prompt": "Hello",
                "response": "World",
                "rating": "up",
                "consent_given": False,
            })
        secret_key = "sk-" + "testsecretkey1234567890"
        aws_key = "AKIA" + "IOSFODNN7EXAMPLE12"
        entry = self.feedback.submit_feedback({
            "prompt": f"My key is {secret_key} email alice@example.com phone +1-555-345-6789",
            "response": f"Do not expose {aws_key}",
            "rating": "up",
            "consent_given": True,
        }, source="local")
        self.assertEqual(entry["status"], "pending")
        self.assertIn("api_key", entry["redactions"])
        self.assertIn("email", entry["redactions"])
        self.assertIn("phone", entry["redactions"])
        raw_disk = Path(self.paths.training_dir(), "raw_feedback.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(secret_key, raw_disk)
        self.assertNotIn("alice@example.com", raw_disk)
        self.assertNotIn(aws_key, raw_disk)
        with self.assertRaises(ValueError):
            self.feedback.submit_feedback({"prompt": "hi\x00evil", "response": "ok", "consent_given": True})
    def test_dataset_sft_dpo_strictness_and_revocation(self):
        e_up = self.feedback.submit_feedback({
            "prompt": "Explain Python GIL",
            "response": "The GIL is a mutex protecting Python object access.",
            "rating": "up",
            "consent_given": True,
            "contributor_id": "user-a",
        })
        e_dup = self.feedback.submit_feedback({
            "prompt": "Explain Python GIL",
            "response": "The GIL is a mutex protecting Python object access.",
            "rating": "up",
            "consent_given": True,
            "contributor_id": "user-a",
        })
        e_down_nocorr = self.feedback.submit_feedback({
            "prompt": "Bad question",
            "response": "Wrong answer",
            "rating": "down",
            "consent_given": True,
            "contributor_id": "user-b",
        })
        e_down_corr = self.feedback.submit_feedback({
            "prompt": "What is 2+2?",
            "response": "5",
            "better_response": "4",
            "rating": "down",
            "consent_given": True,
            "contributor_id": "user-c",
        })
        for eid in (e_up["id"], e_dup["id"], e_down_nocorr["id"], e_down_corr["id"]):
            self.feedback.review_feedback(eid, "approve")
        sft = self.feedback.build_dataset_snapshot(task="sft", name="sft-check")
        self.assertEqual(sft["record_count"], 2)
        self.assertEqual(sft["dedup_removed"], 1)
        self.assertEqual(sft["skip_reasons"].get("downvote_without_correction"), 1)
        self.assertTrue(sft["soup_validation"]["ok"])
        dpo = self.feedback.build_dataset_snapshot(task="dpo", name="dpo-check")
        self.assertEqual(dpo["record_count"], 1)
        dpo_rows = self.feedback.preview_dataset_records(dpo["snapshot_id"])
        self.assertEqual(dpo_rows[0]["chosen"], "4")
        self.assertEqual(dpo_rows[0]["rejected"], "5")
        rev = self.feedback.revoke_contributor("user-c", delete_existing=True)
        self.assertEqual(rev["deleted_count"], 1)
        with self.assertRaises(PermissionError):
            self.feedback.submit_feedback({
                "prompt": "New",
                "response": "Answer",
                "consent_given": True,
                "contributor_id": "user-c",
            })
class TestTrainingOrchestrationAndDeploy(IsolatedShardEnvMixin, unittest.TestCase):
    def _seed_dataset(self):
        e = self.feedback.submit_feedback({
            "prompt": "Write binary search",
            "response": "def bs(a, x): ...",
            "rating": "up",
            "consent_given": True,
        })
        self.feedback.review_feedback(e["id"], "approve")
        return self.feedback.build_dataset_snapshot(task="sft", base_model="TinyLlama/TinyLlama-1.1B-Chat-v1.0")
    def test_end_to_end_train_eval_prepare_approve_deploy_rollback(self):
        ds = self._seed_dataset()
        with mock.patch.object(self.training.engine_instance, "get_status", return_value={"status": "ready", "active_model": "TinyLlama/TinyLlama-1.1B-Chat-v1.0", "is_loaded": True}), \
             mock.patch.object(self.training.engine_instance, "unload_model") as mock_unload:
            dry = self.training.start_training_job(
                base_model="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
                dataset_snapshot_id=ds["snapshot_id"],
                dry_run=True,
                unload_inference=True,
            )
            mock_unload.assert_called_once()
            self.assertEqual(dry["status"], "completed")
            self.assertFalse(dry["training_completed"])
        job = self.training.start_training_job(
            base_model="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
            dataset_snapshot_id=ds["snapshot_id"],
            dry_run=False,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            cur = self.training.get_training_job(job["job_id"])
            if cur["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)
        self.assertEqual(cur["status"], "completed")
        self.assertTrue(cur["training_completed"])
        self.assertIsNotNone(cur["model_version_id"])
        self.assertEqual(cur["progress"]["last_loss"], 0.42)
        vid = cur["model_version_id"]
        ver = self.training.get_model_version(vid)
        self.assertEqual(ver["approval_status"], "pending")
        self.assertFalse(ver["deployed"])
        with self.assertRaises(PermissionError):
            self.training.deploy_version(vid, load_into_engine=False, require_approval=True)
        evaled = self.training.evaluate_version(vid)
        self.assertEqual(evaled["eval_status"], "passed")
        self.assertTrue(evaled["comparison"]["improved"])
        self.assertAlmostEqual(evaled["comparison"]["delta_accuracy"], 0.35, places=3)
        prepared_m = self.training.prepare_version_artifact(vid, target="merged")
        self.assertTrue(os.path.isdir(prepared_m["merged_dir"]))
        prepared_g = self.training.prepare_version_artifact(vid, target="gguf", quant="q4_k_m")
        self.assertTrue(os.path.isfile(prepared_g["gguf_path"]))
        self.training.approve_version(vid, approved=True, notes="verified")
        self.training.engine_instance.active_model_id = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
        with mock.patch.object(self.training.engine_instance, "load_model", return_value={"status": "loading", "mode": "GGUF"}) as mock_load:
            dep = self.training.deploy_version(vid, prefer="gguf", load_into_engine=True)
            mock_load.assert_called_once_with(prepared_g["gguf_path"])
            self.assertEqual(dep["status"], "deployed")
            self.assertEqual(self.training.list_model_versions()["active_version_id"], vid)
            rb = self.training.rollback_deployment(load_into_engine=True)
            self.assertEqual(rb["status"], "rolled_back")
            self.assertEqual(rb["restored_model"], "TinyLlama/TinyLlama-1.1B-Chat-v1.0")
            self.assertIsNone(self.training.list_model_versions()["active_version_id"])
    def test_job_cancellation_and_concurrency_guard(self):
        ds = self._seed_dataset()
        os.environ["MOCK_SOUP_MODE"] = "slow"
        job = self.training.start_training_job(
            base_model="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
            dataset_snapshot_id=ds["snapshot_id"],
        )
        with self.assertRaises(RuntimeError):
            self.training.start_training_job(
                base_model="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
                dataset_snapshot_id=ds["snapshot_id"],
            )
        cancelled = self.training.cancel_training_job(job["job_id"])
        self.assertEqual(cancelled["status"], "cancelled")
class TestApiAuthorizationAndCli(IsolatedShardEnvMixin, unittest.TestCase):
    def test_remote_client_cannot_access_owner_training_endpoints(self):
        remote_client = TestClient(self.server.app, client=("192.168.1.50", 51000))
        local_client = TestClient(self.server.app, client=("127.0.0.1", 51000))
        owner_tok = self.training.get_owner_token()
        for method, path in (
            ("GET", "/api/training/overview"),
            ("GET", "/api/training/feedback"),
            ("POST", "/api/training/privacy"),
            ("POST", "/api/training/datasets/build"),
            ("POST", "/api/training/jobs/start"),
            ("POST", "/api/training/models/v1/deploy"),
            ("POST", "/api/training/models/rollback"),
        ):
            r = remote_client.request(method, path, json={})
            self.assertEqual(r.status_code, 403, f"Expected 403 for remote {method} {path}")
        spoof = local_client.get("/api/training/overview", headers={"X-Forwarded-For": "203.0.113.10"})
        self.assertEqual(spoof.status_code, 403)
        denied_fb = remote_client.post("/api/feedback/submit", json={"prompt": "p", "response": "r", "consent_given": True})
        self.assertEqual(denied_fb.status_code, 403)
        ok_ov = local_client.get("/api/training/overview")
        self.assertEqual(ok_ov.status_code, 200)
        en = local_client.post("/api/training/privacy", json={"allow_remote_feedback": True})
        self.assertEqual(en.status_code, 200)
        ok_remote_fb = remote_client.post("/api/feedback/submit", json={
            "prompt": "Hello remotely",
            "response": "Hi there",
            "rating": "up",
            "consent_given": True,
        })
        self.assertEqual(ok_remote_fb.status_code, 200)
        ok_token_ov = remote_client.get("/api/training/overview", headers={"X-Shard-Owner-Token": owner_tok})
        self.assertEqual(ok_token_ov.status_code, 200)
    def test_cli_training_commands(self):
        self.cli.main(["training", "privacy", "--require-review", "off"])
        self.cli.main(["training", "feedback", "add", "--prompt", "CLI q", "--response", "CLI a", "--consent"])
        self.cli.main(["training", "dataset", "build", "--task", "sft", "--name", "cli-ds"])
        datasets = self.feedback.list_datasets()
        self.assertEqual(len(datasets), 1)
        self.cli.main(["training", "status"])
        self.cli.main(["training", "doctor"])
if __name__ == "__main__":
    unittest.main(verbosity=2)
