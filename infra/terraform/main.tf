terraform {
  required_version = ">= 1.5"
}

variable "region" {
  type    = string
  default = "ru-central1"
}

# Placeholder resources for prod VPS / network / firewall.
# Replace with cloud provider modules (Yandex Cloud / Hetzner / etc.).

output "notes" {
  value = "Wire provider credentials and instance modules before apply"
}
