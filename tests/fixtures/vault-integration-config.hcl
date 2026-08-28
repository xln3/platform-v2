disable_mlock = true
api_addr      = "https://127.0.0.1:8200"

storage "inmem" {}

listener "tcp" {
  address         = "0.0.0.0:8200"
  tls_cert_file   = "/vault/tls/server.crt"
  tls_key_file    = "/vault/tls/server.key"
  tls_min_version = "tls12"
}
