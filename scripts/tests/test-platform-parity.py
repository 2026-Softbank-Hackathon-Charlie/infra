"""Verify parity activation changes only the API and rejects unsafe modes."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
HELM = os.environ.get("HELM", "helm")
VALUES = ROOT / "clusters/aws-dev-management/values/hopcharlie-platform.yaml"


def render(values, check=True):
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "values.json"
        path.write_text(json.dumps(values), encoding="utf-8")
        result = subprocess.run([HELM, "template", "hopcharlie-platform",
            str(ROOT / "helm/charts/hopcharlie-platform"), "-f", str(path)],
            capture_output=True, text=True, encoding="utf-8", check=check)
        if not check:
            return result
        return {(d["kind"], d["metadata"]["name"]): d
                for d in yaml.safe_load_all(result.stdout) if d}


class ParityActivation(unittest.TestCase):
    def setUp(self):
        self.values = json.loads(VALUES.read_text(encoding="utf-8"))

    def test_activation_is_limited_to_api(self):
        enabled = render(self.values)
        off = copy.deepcopy(self.values)
        off["api"]["parity"]["enabled"] = False
        disabled = render(off)
        self.assertEqual(set(enabled) - set(disabled), {("ConfigMap", "hopcharlie-parity")})
        changed = {key for key in disabled if enabled[key] != disabled[key]}
        self.assertEqual(changed, {("Deployment", "hopcharlie-api")})
        pod = enabled[("Deployment", "hopcharlie-api")]["spec"]["template"]
        api = pod["spec"]["containers"][0]
        self.assertIn({"name": "PLATFORM_PARITY_FILE", "value": "/etc/parity/parity.json"}, api["env"])
        self.assertIn({"name": "parity", "mountPath": "/etc/parity", "readOnly": True}, api["volumeMounts"])
        self.assertIn({"name": "parity", "configMap": {"name": "hopcharlie-parity"}}, pod["spec"]["volumes"])
        config = json.loads(enabled[("ConfigMap", "hopcharlie-parity")]["data"]["parity.json"])
        self.assertEqual(config, self.values["api"]["parity"]["config"])

    def test_config_change_rolls_api(self):
        before = render(self.values)[("Deployment", "hopcharlie-api")]
        self.values["api"]["parity"]["config"]["apps"][0]["environments"]["eks"]["backup"]["maxAge"] = "25h"
        after = render(self.values)[("Deployment", "hopcharlie-api")]
        self.assertNotEqual(before["spec"]["template"]["metadata"]["annotations"]["checksum/parity"],
                            after["spec"]["template"]["metadata"]["annotations"]["checksum/parity"])

    def test_invalid_mode_or_missing_apps_is_rejected(self):
        for change in (lambda c: c.update(mode="enforce"), lambda c: c.update(apps=[])):
            values = copy.deepcopy(self.values)
            change(values["api"]["parity"]["config"])
            self.assertNotEqual(render(values, check=False).returncode, 0)


if __name__ == "__main__":
    unittest.main()
