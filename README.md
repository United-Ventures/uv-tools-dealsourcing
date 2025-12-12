# uvds-backend
uvds-backend


# how to update 

az login
az acr login --name uvregistrygfiacconi

docker buildx build \
  --platform linux/amd64 \
  -t uvregistrygfiacconi.azurecr.io/uv-backend:["DA MODIFICARE" es. v6] \
  . \
  --push


az containerapp update \
  --name uv-backend \
  --resource-group uv-rg \
  --image uvregistrygfiacconi.azurecr.io/uv-backend:["DA MODIFICARE" es. v6 ]

