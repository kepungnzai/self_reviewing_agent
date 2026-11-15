# Zero-Dependency Python 3.12 "Hello World" on Google Cloud Run

This repository contains an ultra-lightweight, zero-dependency Python 3.12 web service deployed on Google Cloud Run using Terraform. It uses Python's built-in `http.server` module, eliminating third-party frameworks (like Flask or Gunicorn) to minimize container size, speed up cold starts, and prevent security vulnerabilities.

## File Structure

- `main.py`: Python web application using the built-in `http.server` module.
- `test_main.py`: Offline unit tests using standard library tools.
- `Dockerfile`: Container definition using Python 3.12 slim (no `pip install` step).
- `main.tf`: Terraform configuration for GCP resources.
- `variables.tf`: Terraform input variables.
- `outputs.tf`: Terraform outputs.
- `terraform.tfvars`: Local deployment variables.

---

## Local Development & Testing

### 1. Run Unit Tests
You can run the offline unit tests using Python's built-in `unittest` framework:
```bash
python3 -m unittest test_main.py
```

### 2. Local Verification with Docker
Build and run the container locally:
```bash
# Build the Docker image
docker build -t gcp-hello-world:local .

# Run the container mapping port 8080
docker run --rm -p 8080:8080 -e PORT=8080 gcp-hello-world:local
```

In another terminal, query the service:
```bash
curl -i http://localhost:8080
```
**Expected Output:**
```text
HTTP/1.0 200 OK
Server: BaseHTTP/...
Content-Type: text/plain; charset=utf-8
Content-Length: 13

Hello, World!
```

---

## Deployment to GCP

To avoid the "chicken-and-egg" dependency where Cloud Run requires a container image that hasn't been built yet, follow this 3-phase deployment process.

### Prerequisites
1. Install [Terraform](https://developer.hashicorp.com/terraform/downloads).
2. Install the [Google Cloud SDK (gcloud)](https://cloud.google.com/sdk/docs/install).
3. Authenticate with GCP:
   ```bash
   gcloud auth login
   gcloud auth application-default login
   ```
4. Update `terraform.tfvars` with your GCP Project ID:
   ```hcl
   project_id = "your-gcp-project-id"
   region     = "us-central1"
   ```

### Phase 1: Provision the Artifact Registry Repository
Initialize Terraform and provision only the Artifact Registry repository:
```bash
terraform init
terraform apply -target=google_artifact_registry_repository.repo -auto-approve
```

### Phase 2: Build and Push the Docker Image
1. Authenticate Docker to your GCP Artifact Registry:
   ```bash
   gcloud auth configure-docker us-central1-docker.pkg.dev
   ```
   *(Replace `us-central1` with your configured region if different)*

2. Build and push the container image:
   ```bash
   # Retrieve the repository URL from Terraform outputs
   REPO_URL=$(terraform output -raw repository_url)

   # Build the image for the target architecture (linux/amd64)
   docker build --platform linux/amd64 -t $REPO_URL/hello-world:latest .

   # Push the image to Artifact Registry
   docker push $REPO_URL/hello-world:latest
   ```

### Phase 3: Complete the Infrastructure Deployment
Run a full Terraform apply to provision the Cloud Run service and configure public access:
```bash
terraform apply -auto-approve
```

---

## Verification

Retrieve the live URL from Terraform outputs and send a request:
```bash
SERVICE_URL=$(terraform output -raw service_url)
curl -i $SERVICE_URL
```

**Expected Output:**
```text
HTTP/2 200
content-type: text/plain; charset=utf-8
...

Hello, World!
```

---

## Teardown

To completely remove all resources and avoid any residual storage or management costs, run:
```bash
terraform destroy -auto-approve
```
This will delete the Cloud Run service, the service account, and the Artifact Registry repository (including the stored container image), ensuring zero ongoing costs.
