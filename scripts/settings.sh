#!/usr/bin/env bash
##
## `scripts/settings.sh`: Contains any optional, per-repo script settings/
## customizations.
##

settings::pre_release() {
  base::log_info "Updating version in pyproject.toml..."
  poetry version "${_arg__new_version}"
  git add pyproject.toml
}
