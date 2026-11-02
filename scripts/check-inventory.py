#!/usr/bin/env python3
"""추출본의 파일별 README 목록과 로컬 Terraform 참조를 확인합니다."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def main():
    readme = (ROOT / "README.md").read_text()
    files = [p for p in ROOT.rglob("*") if p.is_file() and not any(x in p.relative_to(ROOT).parts for x in (".git", ".terraform", "__pycache__", ".generated"))]
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        assert f"]({rel})" in readme, f"README missing: {rel}"
        assert not path.name.endswith((".tfvars", ".tfstate", ".tfplan", ".pyc")), f"Local artifact: {rel}"
        assert path.name not in {"backend.hcl", ".retired", "kubeconfig"}, f"Local artifact: {rel}"
        if path.suffix == ".tf":
            text = path.read_text()
            for source in re.findall(r'source\s*=\s*"(\.[^"]+)"', text):
                assert (path.parent / source).is_dir(), f"Missing module {source}: {rel}"
            for source in re.findall(r'file\("\$\{path.module\}/([^"]+)"\)', text):
                assert (path.parent / source).is_file(), f"Missing file {source}: {rel}"
    print(f"Inventory passed: {len(files)} documented files; local Terraform module/file references resolved.")


if __name__ == "__main__":
    main()
