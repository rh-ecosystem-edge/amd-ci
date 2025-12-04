FROM quay.io/openshift/origin-cli:4.20 as oc-cli
FROM quay.io/karmab/kcli:latest

LABEL org.opencontainers.image.authors="Red Hat Ecosystem Engineering"

USER root
# Copying oc binary
COPY --from=oc-cli /usr/bin/oc /usr/bin/oc

# Install Go and operator-sdk
RUN apt-get update && apt-get install -y golang curl && apt-get clean

ARG OPERATOR_SDK_VERSION=v1.6.2
RUN ARCH=$(case $(uname -m) in x86_64) echo -n amd64 ;; aarch64) echo -n arm64 ;; *) echo -n $(uname -m) ;; esac) && \
    OS=$(uname | awk '{print tolower($0)}') && \
    OPERATOR_SDK_DL_URL=https://github.com/operator-framework/operator-sdk/releases/download/${OPERATOR_SDK_VERSION} && \
    curl -fLO ${OPERATOR_SDK_DL_URL}/operator-sdk_${OS}_${ARCH} && \
    chmod +x operator-sdk_${OS}_${ARCH} && \
    mv operator-sdk_${OS}_${ARCH} /usr/local/bin/operator-sdk

# Get the source code in there
WORKDIR /root/amd-ci

ENV GOCACHE=/root/amd-ci/tmp/
ENV PATH="${PATH}:/root/go/bin"

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
