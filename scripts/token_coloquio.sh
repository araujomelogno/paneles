TOKEN_COLOQUIO="$(gcloud sql generate-login-token \
  --impersonate-service-account=coloquio-app@gestion-paneles.iam.gserviceaccount.com)"
export DSN_BOVEDA_COLOQUIO="postgresql://coloquio-app%40gestion-paneles.iam:${TOKEN_COLOQUIO}@127.0.0.1:5432/paneles_boveda?sslmode=disable"
