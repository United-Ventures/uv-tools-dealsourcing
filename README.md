# UV Deal Sourcing Tool  BACKEND
## Backend – Build & Deploy Guide (Azure Container Apps)

This document describes how to **build, push, and deploy** the frontend of the **UV Deal Sourcing Tool** using **Docker Buildx**, **Azure Container Registry (ACR)**, and **Azure Container Apps**.

---

## Prerequisites

Make sure you have the following installed and properly configured:

- Azure CLI (`az`)
- Docker Desktop with `buildx` enabled
- Access to the correct Azure subscription
- Permissions for:
  - Azure Container Registry: `uvregistrygfiacconi`
  - Resource Group: `uv-rg`
  - Azure Container App: `uv-frontend`

---

## 1. Azure Login

Login to Azure:

```bash
az login
```

Login to the Azure Container Registry:

```bash
az acr login --name uvregistrygfiacconi
```

---

## 2. Build & Push the Frontend Docker Image

Move to the frontend directory:


Build the Docker image targeting `linux/amd64` (required when working on Apple Silicon / ARM Macs) and push it to ACR:

```bash
docker buildx build   --platform linux/amd64   -t uvregistrygfiacconi.azurecr.io/uv-backend:v7   .   --push
```

> **Note**  
> Always use `--platform linux/amd64` to avoid deployment issues on Azure Container Apps.

---

## 3. Deploy to Azure Container Apps

Update the existing Container App with the new image:

```bash
az containerapp update   --name uv-frontend   --resource-group uv-rg   --image uvregistrygfiacconi.azurecr.io/uv-backend:v7
```

Azure will automatically create a **new revision** and route traffic to the updated version.

---

## 4. Deployment Verification (Optional but Recommended)

### List active revisions

```bash
az containerapp revision list   --name uv-backend   --resource-group uv-rg   -o table
```

### Retrieve the public frontend URL

```bash
az containerapp show   --name uv-backend   --resource-group uv-rg   --query properties.configuration.ingress.fqdn -o tsv
```

---

## Best Practices

- Keep **frontend and backend versions aligned** (e.g. `v6`, `v6.1`, etc.)
- Always use **versioned image tags** (avoid using `latest` in production)
- Rollbacks are easy: redeploy a previous image version
- Never commit secrets to the repository  
  Use **Azure Container App secrets / environment variables** instead

---

© United Ventures – Internal Tool  
UV Deal Sourcing Platform
