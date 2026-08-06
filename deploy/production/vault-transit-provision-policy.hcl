# Separate admission authority: create one non-exportable key per admitted
# account, rotate its KEK version and configure rollback bounds after every
# profile DEK has been rewrapped. Do not assign this policy to the API.
path "transit/keys/geo-platform-profile-*" {
  capabilities = ["create", "update"]
}

path "transit/keys/geo-platform-profile-*/config" {
  capabilities = ["update"]
}
