FROM quay.io/openshift/origin-cli:4.20 as oc-cli
FROM registry.access.redhat.com/ubi9/go-toolset:1.24.4

LABEL org.opencontainers.image.authors="Red Hat Ecosystem Engineering"

USER root
# Copying oc binary
COPY --from=oc-cli /usr/bin/oc /usr/bin/oc

# Install dependencies: `operator-sdk`
ARG OPERATOR_SDK_VERSION=v1.6.2
RUN ARCH=$(case $(uname -m) in x86_64) echo -n amd64 ;; aarch64) echo -n arm64 ;; *) echo -n $(uname -m) ;; esac) && \
    OS=$(uname | awk '{print tolower($0)}') && \
    OPERATOR_SDK_DL_URL=https://github.com/operator-framework/operator-sdk/releases/download/${OPERATOR_SDK_VERSION} && \
    curl -fLO ${OPERATOR_SDK_DL_URL}/operator-sdk_${OS}_${ARCH} && \
    chmod +x operator-sdk_${OS}_${ARCH} && \
    mv operator-sdk_${OS}_${ARCH} /usr/local/bin/operator-sdk

# Install dependencies for cluster-provision (SSH client for remote deployments)
# python3.12 is required for kcli
# genisoimage is required for kcli to create cloud-init ISOs
# We manually add the CentOS Stream CRB, BaseOS, and AppStream repositories to resolve all libvirt dependencies
# We swap conflicting UBI packages with CentOS Stream versions before the main install to resolve the openssl-fips-provider conflict
RUN echo -e '[centos-stream-crb]\nname=CentOS Stream 9 - CRB\nbaseurl=https://mirror.stream.centos.org/9-stream/CRB/x86_64/os/\ngpgcheck=0\nenabled=1\n\n[centos-stream-baseos]\nname=CentOS Stream 9 - BaseOS\nbaseurl=https://mirror.stream.centos.org/9-stream/BaseOS/x86_64/os/\ngpgcheck=0\nenabled=1\n\n[centos-stream-appstream]\nname=CentOS Stream 9 - AppStream\nbaseurl=https://mirror.stream.centos.org/9-stream/AppStream/x86_64/os/\ngpgcheck=0\nenabled=1' > /etc/yum.repos.d/centos-stream.repo && \
    dnf swap -y openssl-fips-provider-so openssl-fips-provider --allowerasing && \
    dnf install -y --allowerasing python3-pip python3.12 python3.12-pip openssh-clients make libvirt-devel gcc python3.12-devel pkgconf-pkg-config genisoimage && \
    ln -sf /usr/bin/python3.12 /usr/local/bin/python && \
    dnf clean all

# Install kcli from GitHub at a pinned commit and libvirt-python
# Pinned to commit ea18b6f (Jan 23, 2026) to avoid batch/{{ version }} bug
ARG KCLI_COMMIT=ea18b6f853905832f02abc765014bbdbc48d29bd
RUN python3.12 -m pip install git+https://github.com/karmab/kcli.git@${KCLI_COMMIT} libvirt-python && \
    python3 -m pip install git+https://github.com/karmab/kcli.git@${KCLI_COMMIT} libvirt-python || true

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

# Install Python dependencies
RUN python3 -m pip install -r cluster-provision/requirements.txt && \
    python3 -m pip install -r workflows/gpu_operator_versions/requirements.txt

# RUN make install-ginkgo
RUN mkdir -p "${ARTIFACT_DIR}" && chmod 777 "${ARTIFACT_DIR}"
RUN mkdir -p "${GOCACHE}" && chmod 777 "${GOCACHE}"
# Make workspace writable for OpenShift's arbitrary user IDs
# Note: SSH keys should NOT be in the image - they're mounted at runtime from secrets
RUN chmod 777 /root/amd-ci -R
ARG GPU_OPERATOR_VERSION=v23.9.1

ENTRYPOINT ["bash"]
