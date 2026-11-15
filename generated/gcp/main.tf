terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Enable required APIs
resource "google_project_service" "run_api" {
  service            = "run.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "artifactregistry_api" {
  service            = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

# Artifact Registry Repository
resource "google_artifact_registry_repository" "repo" {
  location      = var.region
  repository_id = var.repository_id
  description   = "Docker repository for Hello World service"
  format        = "DOCKER"

  depends_on = [google_project_service.artifactregistry_api]
}

# Dedicated Service Account for Cloud Run
resource "google_service_account" "run_sa" {
  account_id   = "hello-runner"
  display_name = "Cloud Run Hello World Runner"
}

# Cloud Run Service
resource "google_cloud_run_v2_service" "hello_service" {
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.run_sa.email
    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.repo.repository_id}/hello-world:${var.image_tag}"
      ports {
        container_port = 8080
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }
    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }
  }

  depends_on = [
    google_project_service.run_api,
    google_artifact_registry_repository.repo,
    google_service_account.run_sa
  ]
}

# Allow public unauthenticated access to the Cloud Run service
resource "google_cloud_run_v2_service_iam_member" "public_access" {
  name     = google_cloud_run_v2_service.hello_service.name
  location = google_cloud_run_v2_service.hello_service.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
