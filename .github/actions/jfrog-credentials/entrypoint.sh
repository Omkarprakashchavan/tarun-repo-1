#!/bin/bash

set -e

if [[ "${JFROG_URL}" == "AURBA_URL" ]]; then
  echo "Printing JFROG CRED for ARUBA"
  echo "JFROG_USERNAME='ARUBA_USERNAME'" >> $GITHUB_OUTPUT
  echo "JFROG_PASSWORD='ARUBA_PASSWORD')" >> $GITHUB_OUTPUT
  # echo "JFROG_USERNAME=$(echo "${SECRETS}" | jq -r .ARUBA_JFROG_USERNAME)" >> $GITHUB_OUTPUT
  # echo "JFROG_PASSWORD=$(echo "${SECRETS}" | jq -r .ARUBA_JFROG_PASSWORD)" >> $GITHUB_OUTPUT
elif [[ "${JFROG_URL}" == "CSS_URL" ]]; then
  echo "JFROG_USERNAME=$(echo "${SECRETS}" | jq -r .CSS_JFROG_USERNAME)" >> $GITHUB_OUTPUT
  echo "JFROG_PASSWORD=$(echo "${SECRETS}" | jq -r .CSS_JFROG_PASSWORD)" >> $GITHUB_OUTPUT
else
  echo "JFROG_USERNAME=$(echo "${SECRETS}" | jq -r .ARUBA_JFROG_USERNAME)" >> $GITHUB_OUTPUT
  echo "JFROG_PASSWORD=$(echo "${SECRETS}" | jq -r .ARUBA_JFROG_PASSWORD)" >> $GITHUB_OUTPUT
fi
