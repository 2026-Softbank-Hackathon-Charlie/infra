"""CI 배포 진입 조건을 이벤트별로 검증합니다. AWS에 연결하지 않습니다."""
from pathlib import Path
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[2]
class WorkflowGate(unittest.TestCase):
    def test_event_matrix(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/terraform-check.yml').read_text())
        job = workflow['jobs']['deploy']
        expression = job['if'].replace('github.event_name','event').replace('github.ref','ref').replace('inputs.apply','apply').replace('&&','and').replace('true','True')
        for event in ('push','pull_request','workflow_dispatch'):
            for ref in ('refs/heads/main','refs/heads/feature'):
                for apply in (False,True):
                    self.assertEqual(eval(expression,{'__builtins__':{}},{'event':event,'ref':ref,'apply':apply}),event=='workflow_dispatch' and ref=='refs/heads/main' and apply)
        self.assertEqual(job['needs'],'check')
        self.assertFalse(job['concurrency']['cancel-in-progress'])
        self.assertNotIn('id-token',workflow['jobs']['check'].get('permissions',{}))
        triggers = workflow.get('on',workflow.get(True))
        self.assertFalse(triggers['workflow_dispatch']['inputs']['apply']['default'])
if __name__ == '__main__': unittest.main()
