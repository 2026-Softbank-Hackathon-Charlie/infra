output "vpc_id" {
  description = "두 EKS stack가 공유할 VPC ID"
  value       = aws_vpc.shared.id
}
output "public_subnet_ids_by_az" {
  description = "외부 ALB용 subnet ID의 AZ별 map(string)"
  value       = { for slot, az in local.network_slots : az => aws_subnet.public[slot].id }
}
output "management_subnet_ids_by_az" {
  description = "관리 EKS control plane·노드용 사설 subnet ID의 AZ별 map(string)"
  value       = { for slot, az in local.network_slots : az => aws_subnet.private["management-${slot}"].id }
}
output "workload_subnet_ids_by_az" {
  description = "앱 EKS control plane·노드용 사설 subnet ID의 AZ별 map(string)"
  value       = { for slot, az in local.network_slots : az => aws_subnet.private["workload-${slot}"].id }
}
output "management_api_source_security_group_id" {
  description = "후속 management stack에서 Worker 송신 ENI에 연결할 추가 SG ID"
  value       = aws_security_group.management_api_source.id
}
output "workload_api_target_security_group_id" {
  description = "후속 workload stack에서 control plane에 연결할 추가 SG ID"
  value       = aws_security_group.workload_api_target.id
}
output "management_cluster_name" {
  description = "subnet discovery 태그와 일치하는 관리 EKS 이름"
  value       = var.management_cluster_name
}
output "workload_cluster_name" {
  description = "subnet discovery 태그와 일치하는 앱 EKS 이름"
  value       = var.workload_cluster_name
}
output "loki_bucket_name" {
  description = "Loki values loki.storage.bucketNames 값"
  value       = aws_s3_bucket.loki.bucket
}
output "loki_role_arn" {
  description = "Loki Pod Identity 연결에 쓸 역할"
  value       = aws_iam_role.loki.arn
}
output "cnpg_bucket_name" {
  description = "CNPG PoC source/ 및 restore/ 백업용 전용 S3 버킷 이름"
  value       = aws_s3_bucket.cnpg.bucket
}
output "cnpg_source_role_arn" {
  description = "workload charlie-db/postgres-poc Pod Identity 역할"
  value       = aws_iam_role.cnpg["source"].arn
}
output "cnpg_restore_role_arn" {
  description = "source archive 읽기와 restore/ 작업만 허용한 복원 Pod Identity 역할"
  value       = aws_iam_role.cnpg["restore"].arn
}
