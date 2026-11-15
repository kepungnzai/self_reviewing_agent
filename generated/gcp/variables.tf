variable "project_id" {
  type        = string
  description = "The GCP Project ID where resources will be deployed."
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "The GCP region for resources."
}

variable "repository_id" {
  type        = string
  default     = "hello-world-repo"
  description = "The ID of the Artifact Registry repository."
}

variable "service_name" {
  type        = string
  default     = "hello-world-service"
  description = "The name of the Cloud Run service."
}

variable "image_tag" {
  type        = string
  default     = "latest"
  description = "The tag of the Docker image to deploy."
}
