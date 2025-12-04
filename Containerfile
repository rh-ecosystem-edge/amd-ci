FROM quay.io/openshift/origin-cli:4.20 as oc-cli
FROM quay.io/karmab/kcli:latest as kcli-cli
FROM registry.access.redhat.com/ubi9/go-toolset:1.24.4

LABEL org.opencontainers.image.authors="Red Hat Ecosystem Engineering"

USER root
# Copying oc binary
COPY --from=oc-cli /usr/bin/oc /usr/bin/oc

# Copy kcli and its Python packages from the kcli image
# The kcli image has all dependencies pre-installed
COPY --from=kcli-cli /usr/local/bin/kcli /usr/local/bin/kcli
COPY --from=kcli-cli /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# Install dependencies: `operator-sdk`
ARG OPERATOR_SDK_VERSION=v1.6.2
RUN ARCH=$(case $(uname -m) in x86_64) echo -n amd64 ;; aarch64) echo -n arm64 ;; *) echo -n $(uname -m) ;; esac) && \
    OS=$(uname | awk '{print tolower($0)}') && \
    OPERATOR_SDK_DL_URL=https://github.com/operator-framework/operator-sdk/releases/download/${OPERATOR_SDK_VERSION} && \
    curl -fLO ${OPERATOR_SDK_DL_URL}/operator-sdk_${OS}_${ARCH} && \
    chmod +x operator-sdk_${OS}_${ARCH} && \
    mv operator-sdk_${OS}_${ARCH} /usr/local/bin/operator-sdk

# Install dependencies for sno-deployer (SSH client for remote deployments)
# python3.12 is needed for kcli (copied from kcli image which uses Python 3.12)
RUN dnf install -y python3.12 python3.12-pip openssh-clients && \
    ln -sf /usr/bin/python3.12 /usr/local/bin/python && \
    ln -sf /usr/bin/python3.12 /usr/bin/python3 && \
    dnf clean all

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
RUN python3 -m pip install -r sno-deployer/requirements.txt

# RUN make install-ginkgo
RUN mkdir -p "${ARTIFACT_DIR}" && chmod 777 "${ARTIFACT_DIR}"
RUN mkdir -p "${GOCACHE}" && chmod 777 "${GOCACHE}"
RUN chmod 777 /root/amd-ci -R
ARG GPU_OPERATOR_VERSION=v23.9.1

ENTRYPOINT ["bash"]
