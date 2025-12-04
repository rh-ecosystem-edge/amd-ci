FROM quay.io/openshift/origin-cli:4.20 as oc-cli
FROM quay.io/karmab/kcli:latest as kcli-cli
FROM registry.access.redhat.com/ubi9/go-toolset:1.24.4

LABEL org.opencontainers.image.authors="Red Hat Ecosystem Engineering"

USER root
# Copying oc binary
COPY --from=oc-cli /usr/bin/oc /usr/bin/oc
# Copying kcli binary
COPY --from=kcli-cli /usr/local/bin/kcli /usr/local/bin/kcli

# Install dependencies: `operator-sdk`
ARG OPERATOR_SDK_VERSION=v1.6.2
RUN ARCH=$(case $(uname -m) in x86_64) echo -n amd64 ;; aarch64) echo -n arm64 ;; *) echo -n $(uname -m) ;; esac) && \
    OS=$(uname | awk '{print tolower($0)}') && \
    OPERATOR_SDK_DL_URL=https://github.com/operator-framework/operator-sdk/releases/download/${OPERATOR_SDK_VERSION} && \
    curl -fLO ${OPERATOR_SDK_DL_URL}/operator-sdk_${OS}_${ARCH} && \
    chmod +x operator-sdk_${OS}_${ARCH} && \
    mv operator-sdk_${OS}_${ARCH} /usr/local/bin/operator-sdk

# Install dependencies for sno-deployer (SSH client for remote deployments)
RUN dnf install -y python3-pip openssh-clients && dnf clean all

# Get the source code in there
WORKDIR /root/amd-ci

ENV GOCACHE=/root/amd-ci/tmp/
ENV PATH="${PATH}:/opt/app-root/src/go/bin"

# Defaults we want the image to run with, can be overridden
ARG ARTIFACT_DIR=/root/amd-ci/test-results
ENV ARTIFACT_DIR="${ARTIFACT_DIR}"
ENV TEST_TRACE=true
ENV VERBOSE_LEVEL=100
ENV DUMP_FAILED_TESTS=true

COPY . .

# Install sno-deployer Python dependencies
RUN pip3 install -r sno-deployer/requirements.txt

# RUN make install-ginkgo
RUN mkdir -p "${ARTIFACT_DIR}" && chmod 777 "${ARTIFACT_DIR}"
RUN mkdir -p "${GOCACHE}" && chmod 777 "${GOCACHE}"
RUN chmod 777 /root/amd-ci -R
ARG GPU_OPERATOR_VERSION=v23.9.1

ENTRYPOINT ["bash"]