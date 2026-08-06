# API runtime: wrap/unwrap only. It cannot create, configure, export, back up,
# rotate, configure or delete account keys.
path "transit/encrypt/geo-platform-profile-*" {
  capabilities = ["update"]
}

path "transit/decrypt/geo-platform-profile-*" {
  capabilities = ["update"]
}
