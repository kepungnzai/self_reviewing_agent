# Technical Design: Zero-Dependency Python 3.12 "Hello World" on Google Cloud Run

This design implements a zero-dependency, ultra-lightweight "Hello World" web service using Python 3.12's built-in `http.server` module. By eliminating third-party frameworks (like Flask and Gunicorn), we minimize the container footprint, reduce cold start times, and completely eliminate third-party security vulnerabilities.

---

## Resource Inventory

The infrastructure resources managed via Terraform remain identical to the previous iteration, ensuring full lifecycle management and clean teardowns.

| Resource Name (Terraform ID) | GCP Resource Type | Purpose | Why it is needed |
| :--- | :--- | :--- | :--- |
| `google_project_service.run_api` | `run.googleapis.com` | Enables Cloud Run API | Required to deploy and manage serverless container instances. |
| `google_project_service.artifactregistry_api` | `artifactregistry.googleapis.com` | Enables Artifact Registry API | Required to host the containerized application images. |
| `google_artifact_registry_repository.repo` | `google_artifact_registry_repository` | Docker Artifact Repository | Stores the Python 3.12 Docker images securely. |
| `google_service_account.run_sa` | `google_service_account` | Dedicated Service Account | Provides a minimal-privilege runtime identity for the Cloud Run service. |
| `google_cloud_run_v2_service.hello_service` | `google_cloud_run_v2_service` | Cloud Run Service | Runs the containerized Python web application, scaling to zero when idle. |
| `google_cloud_run_v2_service_iam_member.public_access` | `google_cloud_run_v2_service_iam_member` | IAM Policy Binding | Grants public access (`allUsers` -> `roles/run.invoker`) to the service. |

---

## Request Flow

```
[ Client ] 
    │
    ▼ (HTTPS GET)
[ Google Front End (GFE) ]  <-- Handles TLS termination & routing
    │
    ▼ (Routes to Instance)
[ Cloud Run Container Instance ] 
    │
    ▼ (Port 8080)
[ Python 3.12 Built-in http.server ] <-- Zero-dependency web server
    │
    ▼ (Processes Request)
[ Custom HTTP Request Handler (main.py) ]
```

### Step-by-Step Execution:
1. **Client Request:** The client sends an HTTPS GET request to the Cloud Run service URL.
2. **Routing & TLS:** The Google Front End (GFE) intercepts the request, terminates TLS, and routes the traffic to the Cloud Run service.
3. **Scale-from-Zero (Cold Start):** If no instances are active, the Cloud Run control plane spins up a new container instance. Because the image contains no third-party dependencies, the container size is minimized, resulting in an exceptionally fast cold start.
4. **Application Startup:** The container starts the Python process directly, running the built-in `http.server` bound to the port specified by the `$PORT` environment variable (default: `8080`).
5. **Request Handling:** The built-in server routes the incoming request to our custom request handler in `main.py`.
6. **Response:** The handler returns exactly the plain text `Hello, World!` with a `text/plain` MIME type and HTTP status 200.

---

## Identity and Permissions

To adhere to the principle of least privilege, we bypass the default Compute Engine service account and provision a dedicated runtime identity.

### 1. Deployer Identity (Local CLI or CI/CD)
* **Role(s) Required:** 
  * `roles/run.admin` (to manage Cloud Run services)
  * `roles/artifactregistry.admin` (to manage repositories and push images)
  * `roles/iam.serviceAccountAdmin` (to create and manage the runtime service account)
  * `roles/resourcemanager.projectIamAdmin` (to assign IAM roles)

### 2. Cloud Run Runtime Service Account (`hello-runner`)
* **Identity:** `hello-runner@<PROJECT_ID>.iam.gserviceaccount.com`
* **Permissions:** **None**. The "Hello World" application does not interact with other GCP services (such as Cloud Storage or Secret Manager). Therefore, its service account is granted zero GCP IAM roles, ensuring a perfect least-privilege boundary.

### 3. Public Invoker
* **Identity:** `allUsers` (representing any unauthenticated user on the internet)
* **Role Granted:** `roles/run.invoker` on the `hello_service` Cloud Run resource. This allows public HTTP requests to reach the service.

---

## File Layout

By using Python's built-in server, we eliminate `requirements.txt` entirely, simplifying the codebase to the absolute minimum:

* `main.py` - Python application code using the built-in `http.server` module.
* `test_main.py` - Unit tests using standard library tools (e.g., `unittest` and `urllib.request` with a local server thread).
* `Dockerfile` - Lightweight container definition using Python 3.12 slim (no `pip install` step).
* `main.tf` - Terraform configuration for GCP resources (Artifact Registry, Cloud Run, IAM, Service Account).
* `variables.tf` - Terraform input variables.
* `outputs.tf` - Terraform outputs (Repository URL, Cloud Run URL).
* `terraform.tfvars` - Local deployment variables template.
* `README.md` - Complete documentation with local testing, 3-phase deployment, verification, and teardown commands.

---

## Test Plan

### 1. Local Verification
Before deploying to GCP, verify the application locally using Docker:

```bash
# 1. Build the Docker image (notice the lack of dependency installation steps)
docker build -t gcp-hello-world:local .

# 2. Run the container locally, mapping port 8080
docker run --rm -p 8080:8080 -e PORT=8080 gcp-hello-world:local

# 3. Query the service from another terminal
curl -i http://localhost:8080
# Expected Output:
# HTTP/1.0 200 OK
# Server: BaseHTTP/...
# Content-Type: text/plain; charset=utf-8
#
# Hello, World!
```

### 2. Automated Unit Testing
Run the offline unit tests using Python's built-in `unittest` framework:
```bash
python3 -m unittest test_main.py
```

### 3. Post-Deployment Verification
Once Terraform has successfully run:

```bash
# 1. Retrieve the live URL from Terraform outputs
SERVICE_URL=$(terraform output -raw service_url)

# 2. Send a request to the live Cloud Run service
curl -i $SERVICE_URL

# Expected Output:
# HTTP/2 200
# content-type: text/plain; charset=utf-8
#
# Hello, World!
```

---

## Cost at Zero Traffic & Teardown

### Cost at Zero Traffic
* **Cloud Run:** **$0.00**. Cloud Run charges only for CPU and memory allocated during request processing. When scaled to zero, compute costs are strictly zero.
* **Artifact Registry:** **$0.00** (under free tier). GCP Artifact Registry offers 500 MB of free storage per month. Eliminating Flask/Gunicorn reduces the container image size to ~120 MB, fitting comfortably within the free tier. If the free tier is exceeded, storage costs are approximately `$0.10` per GB per month (less than one cent for this image).
* **Total Idle Cost:** **$0.00 / month**.

### Teardown Steps
To completely remove all resources and avoid any residual storage or management costs:

```bash
# Destroy all resources managed by Terraform
terraform destroy -auto-approve
```
*Note: This will delete the Artifact Registry repository, completely removing the container image and ensuring zero ongoing storage fees.*

---

## ADR Concerns

### 1. The "Chicken-and-Egg" Deployment Dependency
A standard Terraform run will fail on the first pass if it attempts to deploy a Cloud Run service pointing to an image that does not yet exist in the Artifact Registry. Conversely, the Artifact Registry repository must exist before the image can be pushed.

* **Mitigation:** The deployment must follow a 3-phase apply process:
  1. Provision only the Artifact Registry repository (`terraform apply -target=google_artifact_registry_repository.repo`).
  2. Build and push the Docker image to the newly created repository.
  3. Run a full `terraform apply` to provision the Cloud Run service and its IAM bindings.

### 2. Production Suitability of `http.server`
Python's built-in `http.server` is single-threaded and not recommended for high-throughput production environments. 

* **Mitigation:** For a simple "Hello World" service with zero third-party dependencies, this is a highly justified trade-off. It guarantees zero idle cost, minimal container footprint, and rapid cold starts. If the service needs to scale to handle high concurrent traffic in the future, the container can be easily refitted with Gunicorn/Uvicorn without changing the underlying GCP infrastructure.