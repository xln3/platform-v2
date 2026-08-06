# Separate deletion authority used only after customer/legal approval and
# dual-control audit. It cannot encrypt, decrypt, export or back up keys.
path "transit/keys/geo-platform-profile-*" {
  capabilities = ["delete"]
}
