ui = false
disable_mlock = false
enable_unauthenticated_access = ["generate-root"]

storage "raft" {
  path    = "/vault/data"
  node_id = "geo-platform-v2-vault-1"
}

listener "tcp" {
  address         = "0.0.0.0:8200"
  cluster_address = "0.0.0.0:8201"
  tls_cert_file   = "/vault/tls/server.crt"
  tls_key_file    = "/vault/tls/server.key"
  tls_min_version = "tls13"
}

api_addr     = "https://127.0.0.1:18200"
cluster_addr = "https://geo-platform-v2-vault:8201"
