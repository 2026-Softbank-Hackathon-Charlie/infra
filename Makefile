SHELL := /bin/bash
.DEFAULT_GOAL := help
export STACK AWS_ACCOUNT_ID
PYTHON ?= python3
.PHONY: clickstack-check config-check tf-verify help check tf-fmt tf-fmt-check tf-check tf-test tf-init tf-plan tf-apply helm-check ci-test inventory-check aws-tunnel argo-port-forward
help:
	@echo 'make check                          정적 검사·mock test·Helm render'
	@echo 'make clickstack-check               ClickStack·operator·수집 계약 검사 (클러스터 접근 없음)'
	@echo 'make tf-init STACK=aws/dev/foundation  실제 backend 초기화'
	@echo 'make tf-plan / tf-apply STACK=... AWS_ACCOUNT_ID=...'
	@echo 'make aws-tunnel [CLUSTER=management|workload]  AWS SSM 터널 (foreground)'
	@echo 'make argo-port-forward                Argo CD → https://localhost:8080'
aws-tunnel argo-port-forward: export CLUSTER ?= management
aws-tunnel argo-port-forward: export AWS_PROFILE ?=
aws-tunnel argo-port-forward: export EKS_LOCAL_PORT ?=
aws-tunnel argo-port-forward: export ARGO_LOCAL_PORT ?=
aws-tunnel:
	@$(PYTHON) scripts/eks-access.py tunnel
argo-port-forward:
	@$(PYTHON) scripts/eks-access.py argo-forward
check: tf-fmt-check tf-verify ci-test helm-check inventory-check config-check
config-check:
	@$(PYTHON) scripts/project-config.py check
tf-verify:
	@$(PYTHON) scripts/terraform-verify.py
tf-fmt:
	terraform fmt -recursive terraform
tf-fmt-check:
	terraform fmt -check -recursive terraform
tf-check: tf-fmt-check
	@bash scripts/terraform-check.sh
tf-test:
	@bash scripts/terraform-test.sh
tf-init:
	@bash scripts/tf-stack.sh init
tf-plan:
	@bash scripts/tf-stack.sh plan
tf-apply:
	@bash scripts/tf-stack.sh apply
helm-check:
	@PYTHON="$(PYTHON)" bash scripts/helm-check.sh
ci-test:
	@$(PYTHON) scripts/test-cnpg-migration-admission.py --check-only
	@$(PYTHON) scripts/tests/test-project-config.py
	@$(PYTHON) scripts/tests/test-terraform-verify.py
	@$(PYTHON) scripts/tests/test-management-scaleout-plan.py
	@$(PYTHON) scripts/tests/test-tf-ci.py
	@$(PYTHON) scripts/tests/test-workflow.py
	@$(PYTHON) scripts/tests/test-eks-access.py
	@$(PYTHON) scripts/tests/test-cnpg-jobs.py
	@$(PYTHON) scripts/tests/test-cnpg-freeze.py
	@$(PYTHON) scripts/tests/test-headlamp.py
	@$(PYTHON) scripts/tests/test-headlamp-gke.py
	@$(PYTHON) scripts/tests/test-headlamp-gke-google.py
	@$(PYTHON) scripts/tests/test-headlamp-google-config.py
	@$(PYTHON) scripts/tests/test-argocd-gcp.py
	@$(PYTHON) scripts/tests/test-app-routing.py
	@$(PYTHON) scripts/tests/test-platform-written-tokens.py
	@$(PYTHON) scripts/tests/test-platform-parity.py
	@$(PYTHON) scripts/tests/test-hopai-recommendations.py
	@$(PYTHON) scripts/tests/test-register-traefik.py
	@$(PYTHON) scripts/tests/test-db-network.py
	@$(PYTHON) scripts/tests/test-db-network-assets.py
	@$(PYTHON) scripts/tests/test-onprem-registration.py
	@$(PYTHON) scripts/tests/test-onprem-settings.py
	@$(PYTHON) scripts/tests/test-onprem-observability-apply.py
	@$(PYTHON) scripts/tests/test-db-migration-access.py
	@$(PYTHON) -m unittest terraform/integrations/aws-gcp/tailscale/tests/test_router_assets.py
	@for script in scripts/*.sh; do bash -n "$$script" || exit; done
inventory-check:
	@$(PYTHON) scripts/check-inventory.py

clickstack-check:
	@$(PYTHON) scripts/check-clickstack.py
