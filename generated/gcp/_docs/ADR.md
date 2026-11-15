# Architectural Decision Record (ADR)

## Context
The goal is to deploy a simple "Hello World" web service written in Python 3.12 on Google Cloud Platform (GCP). The deployment must satisfy the following constraints:
- Incur zero cost at zero traffic.
- Require minimal operational burden.
- Be fully defined via Infrastructure-as-Code (IaC).
- Support easy teardown.

In the previous iteration, we proposed using Flask and Gunicorn as the web application framework and WSGI server. However, the reviewer requested simplifying the architecture by eliminating third-party dependencies, reducing the container image size, and speeding up cold starts.

## Decision
We will deploy the Python 3.12 web service on **Google Cloud Run** and manage the entire infrastructure using **Terraform**. 

To minimize dependencies, container size, and cold start times, we will use Python's built-in **`http.server`** module instead of Flask/Gunicorn.

### Hosting Platform: Google Cloud Run
- **Scale-to-Zero:** Cloud Run natively scales down to zero instances when there is no incoming traffic, ensuring zero compute costs.
- **Container-based:** It allows us to package the Python 3.12 application using a standard Dockerfile, ensuring local parity and flexibility.
- **Low Operational Burden:** As a fully managed serverless platform, GCP handles provisioning, patching, and scaling of the underlying infrastructure.

### Web Server: Python's Built-in `http.server`
- **Zero Third-Party Dependencies:** By using the standard library's `http.server` module, we eliminate the need for external packages like Flask, Gunicorn, and Werkzeug.
- **Reduced Image Size:** The container image only needs the base Python 3.12 slim image without any additional pip installations, leading to a smaller footprint.
- **Faster Cold Starts:** A smaller container image with no external package imports starts up faster, mitigating the cold start penalty when scaling up from zero.

### Infrastructure-as-Code: Terraform
- **Industry Standard:** Terraform is a robust, declarative IaC tool that fully supports GCP resources.
- **State Management & Teardown:** Terraform tracks resource state, allowing for clean, reliable teardowns via `terraform destroy`.

---

## Alternatives Considered

### 1. Flask and Gunicorn
- **Description:** Using Flask as the web framework and Gunicorn as the production WSGI server.
- **Why Rejected:** While Flask and Gunicorn are standard for Python web applications, they introduce external dependencies, increase the container image size, and slightly increase cold start times. For a simple "Hello World" service, Python's built-in `http.server` is sufficient and more efficient.

### 2. Google Compute Engine (GCE) VMs
- **Description:** Deploying the service on a virtual machine.
- **Why Rejected:** GCE VMs do not support scaling to zero cost. Even an idle VM incurs hourly compute charges and persistent disk storage costs. Additionally, managing OS updates, web servers, and scaling policies increases operational burden.

### 3. Google Kubernetes Engine (GKE)
- **Description:** Deploying the service as a containerized application on a managed Kubernetes cluster.
- **Why Rejected:** GKE clusters (both Standard and Autopilot) incur ongoing management fees or minimum node costs even with zero traffic. The operational complexity of managing Kubernetes manifests, ingress controllers, and cluster upgrades is disproportionately high for a simple "Hello World" service.

### 4. Google Cloud Functions (2nd gen)
- **Description:** Deploying the Python code as a serverless function.
- **Why Rejected:** While Cloud Functions (2nd gen) is built on Cloud Run and supports scaling to zero, it restricts the application structure to the Google Functions Framework. Cloud Run is preferred because it allows standard containerization, making the application highly portable and easier to test locally without vendor-specific wrappers.

---

## Consequences

### Positive
- **Zero Idle Cost:** No charges are incurred when the service is not receiving requests.
- **Zero Dependencies:** The application relies entirely on the Python standard library, simplifying maintenance and security auditing.
- **Minimal Container Footprint:** The container image size is kept to an absolute minimum, leading to rapid deployments and faster scale-from-zero response times.
- **Declarative Management:** The entire infrastructure (Artifact Registry, Cloud Run service, IAM bindings) is defined in Terraform, ensuring reproducible environments.
- **Easy Cleanup:** Running `terraform destroy` will cleanly remove all created resources, preventing orphaned costs.

### Negative / Trade-offs
- **Cold Starts:** When scaling up from zero instances, the first request may experience a slight latency penalty (cold start) while the container spins up, though this is minimized by the zero-dependency architecture.
- **Container Registry Dependency:** Cloud Run requires the container image to be hosted in a registry (e.g., Artifact Registry), which may incur negligible storage costs for the stored image, though compute remains free.

---

## Open Risks
- **Artifact Registry Storage Costs:** While Cloud Run compute scales to zero cost, the container image stored in Artifact Registry will incur a very small storage fee (typically pennies per month) unless the registry is also deleted during teardown. We will mitigate this by ensuring the Artifact Registry is managed by Terraform so it is fully removed during `terraform destroy`.