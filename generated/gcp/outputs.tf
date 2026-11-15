output "repository_url" {
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.repo.repository_id}"
  description = "The URL of the Artifact Registry repository."
}

output "service_url" {
  value       = google_cloud_run_v2_service.hello_service.uri
  description = "The URL of the deployed Cloud Run service."
}
